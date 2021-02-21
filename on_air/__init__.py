#!/usr/bin/env python

import argparse
import glob
import json
import logging
import re
import signal
import socket
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, DefaultDict, Dict, Iterable, List, Optional, Tuple

from blink1.blink1 import Blink1
from google.auth import jwt
from google.cloud import pubsub

Payload = Dict[str, Any]

_log = logging.getLogger(__file__)

_LSOF_KNOWN_ERRORS = re.compile(
    r"(?:Output information may be incomplete\.|WARNING: can't stat\(\) .* file system)"
)


class OnAirError(Exception):
    pass


class SystemError(OnAirError):
    pass


def lsof(pattern: str) -> List[str]:
    """List file owner PIDs for the given glob pattern."""
    files = glob.glob(pattern)
    response = subprocess.run(
        ["/usr/bin/lsof", "-t"] + files,
        capture_output=True,
        text=True,
    )
    if response.returncode != 0:
        # Expect some errors, these are fine
        for line in response.stderr.split("\n"):
            if len(line.strip()) == 0:
                continue

            if not _LSOF_KNOWN_ERRORS.search(line):
                _log.error(line)
                raise SystemError(
                    f"Unexpected error from lsof: '{line}', "
                    f"stdout '{response.stdout}', stderr '{response.stderr}'"
                )

    stdout = response.stdout.rstrip()
    if len(stdout) > 0:
        owners = stdout.split("\n")
        return owners

    return []


def poll_av_and_publish(
    poll_interval: int,
    publish_payload: Callable[[Payload], None],
    source_name: str,
) -> None:
    """Poll local audio/video hardware in a loop, and publish update messages."""

    # This will loop forever, so set a nice shutdown handler
    signal.signal(signal.SIGINT, shutdown)

    last_payload: Optional[Payload] = None

    _log.info("Watching for changes in local audio/video state")
    while True:
        _log.debug("Polling local audio/video state")
        audio_owners = lsof("/dev/snd/pcmC*")
        video_owners = lsof("/dev/video*")

        payload = {
            "audio": len(audio_owners) > 0,
            "video": len(video_owners) > 0,
            "source": source_name,
        }

        if payload != last_payload:
            last_payload = payload
            publish_payload(payload)

        time.sleep(poll_interval)


@dataclass
class Config:
    google_credential: str
    google_project_id: str
    poll_interval: int
    topic_name: str
    source_name: str

    @staticmethod
    def from_args(args: argparse.Namespace) -> "Config":
        google_credential = None
        google_project_id = None
        poll_interval = None
        topic_name = None
        source_name = None

        if args.config:
            with open(args.config, "r") as config_file:
                raw_config_json = json.load(config_file)

            google_credential = raw_config_json.get("google_credential")
            google_project_id = raw_config_json.get("google_project_id")
            poll_interval = raw_config_json.get("poll_interval")
            topic_name = raw_config_json.get("topic_name")
            source_name = raw_config_json.get("source_name")

        if args.google_credential:
            google_credential = args.google_credential
        if args.google_project_id:
            google_project_id = args.google_project_id
        if args.poll_interval:
            poll_interval = args.poll_interval
        if args.topic_name:
            topic_name = args.topic_name
        if args.source_name:
            source_name = args.source_name

        if not source_name:
            source_name = socket.gethostname()

        if google_credential is None or not isinstance(google_credential, str):
            raise ValueError(f"Invalid google_credential: '{google_credential}'")
        if google_project_id is None or not isinstance(google_project_id, str):
            raise ValueError(f"Invalid google_project_id: '{google_project_id}'")
        if poll_interval is None or not isinstance(poll_interval, int):
            raise ValueError(f"Invalid poll_interval: '{poll_interval}'")
        if topic_name is None or not isinstance(topic_name, str):
            raise ValueError(f"Invalid topic_name: '{topic_name}'")
        if source_name is None or not isinstance(source_name, str):
            raise ValueError(f"Invalid source_name: '{source_name}'")

        return Config(
            google_credential=google_credential,
            google_project_id=google_project_id,
            poll_interval=poll_interval,
            topic_name=topic_name,
            source_name=source_name,
        )


def run_stream(args: argparse.Namespace) -> None:
    """Entrypoint for the streaming client."""
    config = Config.from_args(args)

    service_account_info = json.load(open(config.google_credential))
    audience = "https://pubsub.googleapis.com/google.pubsub.v1.Publisher"
    credentials = jwt.Credentials.from_service_account_info(
        service_account_info, audience=audience
    )
    publisher = pubsub.PublisherClient(credentials=credentials)
    topic_name = f"projects/{config.google_project_id}/topics/{config.topic_name}"

    def publish_payload(payload: Payload) -> None:
        data = json.dumps(payload)
        _log.info("Publishing message: '%s'", data)
        future = publisher.publish(topic_name, data.encode("utf-8"))
        future.result()

    poll_av_and_publish(
        poll_interval=config.poll_interval,
        publish_payload=publish_payload,
        source_name=config.source_name,
    )


# Time for an interval of blinking, seconds
_BLINK_DURATION = 0.1
# Number of alerting blinks before solid color
_BLINK_REPEAT = 3

Rgb = Tuple[int, int, int]
_RGB_VIDEO = (255, 0, 0)
_RGB_AUDIO = (0, 0, 255)
_RGB_OFF = (0, 0, 0)


_HARDWARE_KEYS = ("audio", "video")


@dataclass(eq=True, frozen=True)
class ComputedState:
    audio: bool
    video: bool

    @staticmethod
    def from_source_states(source_states: Iterable[Payload]) -> "ComputedState":
        audio = False
        video = False

        for state in source_states:
            if state["audio"]:
                audio = True
            if state["video"]:
                video = True

        return ComputedState(audio=audio, video=video)


class DisplayState:
    _device: Optional[Blink1]
    # Map of {source name -> Payload}
    _source_states: DefaultDict[str, Payload]
    # Computed hash over all source states
    _state: ComputedState
    # Last color to display
    _last_color: Rgb

    def __init__(self, device):
        self._device = device
        self._source_states = defaultdict(dict)
        self._state = ComputedState(audio=False, video=False)
        self._last_color = _RGB_OFF

    def __enter__(self, *args):
        return self

    def __exit__(self, *args):
        if self._device:
            self._device.off()

    def _solid(self, color: Rgb) -> None:
        _log.debug("Color set: '%s'", color)
        if self._device:
            self._device.fade_to_rgb(0, *color)

    def _blink(self, color: Rgb) -> None:
        """Blink the given new color, alternating back to the current color."""
        for _ in range(0, _BLINK_REPEAT):
            self._solid(color)
            time.sleep(_BLINK_DURATION)
            self._solid(self._last_color)
            time.sleep(_BLINK_DURATION)

    def update(self, payload: Payload) -> None:
        source_name = payload["source"]
        previous_source_state = self._source_states[source_name]
        if previous_source_state == payload:
            _log.debug("Source state is unchanged: '%s'", payload)
            return
        self._source_states[source_name] = payload

        state = ComputedState.from_source_states(self._source_states.values())
        if self._state == state:
            _log.debug("Computed state is unchanged: '%s'", state)
            return
        self._state = state
        _log.debug("Computed state updated: '%s'", state)

        if state.video:
            color = _RGB_VIDEO
        elif state.audio:
            color = _RGB_AUDIO
        else:
            color = _RGB_OFF

        self._blink(color)
        self._solid(color)
        self._last_color = color


_LISTEN_SKEW = timedelta(minutes=1)


def run_listen(args: argparse.Namespace) -> None:
    """Entrypoint for the listening client."""
    service_account_info = json.load(open(args.google_credential))
    audience = "https://pubsub.googleapis.com/google.pubsub.v1.Subscriber"
    credentials = jwt.Credentials.from_service_account_info(
        service_account_info, audience=audience
    )
    subscriber = pubsub.SubscriberClient(credentials=credentials)
    subscription_name = (
        f"projects/{args.google_project_id}/subscriptions/{args.subscription_name}"
    )

    if args.no_blink1:
        device = None
    else:
        device = Blink1()
        device.off()

    with DisplayState(device) as display_state:

        def recieve_message(message) -> None:
            now = datetime.now(tz=timezone.utc)
            if message.publish_time < (now - _LISTEN_SKEW):
                _log.debug(
                    "Discarding skewed message: now '%s', message '%s'",
                    now,
                    message.publish_time,
                )
                message.ack()
                return

            payload = message.data.decode("utf-8")
            _log.info("Recieved message: %s", payload)
            data = json.loads(payload)
            display_state.update(data)

            message.ack()

        # This will loop forever, so set a nice shutdown handler
        signal.signal(signal.SIGINT, shutdown)

        _log.info("Listening for published updates")
        future = subscriber.subscribe(subscription_name, recieve_message)
        future.result()


def shutdown(signal_number, frame):
    """Shutdown handler for system signals."""
    _log.info(
        "Received signal '%s', shutting down",
        signal_number,
    )
    sys.exit(1)


def main() -> None:
    """Main command line entrypoint."""
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(
        logging.Formatter("%(asctime)s|%(name)s|%(levelname)s|%(message)s")
    )
    root_logger.handlers = [stream_handler]

    parser = argparse.ArgumentParser(description="on-air warning light")
    parser.add_argument(
        "--google-project-id",
        type=str,
        help="Google project id to use",
    )
    parser.add_argument(
        "--google-credential",
        type=str,
        help="Google credential JWT file",
    )

    subparsers = parser.add_subparsers(required=True, dest="subcommand")
    stream = subparsers.add_parser("stream")
    stream.set_defaults(execute=run_stream)
    parser.add_argument(
        "--config",
        type=str,
        help="Config file to use. Does not load a config file by default",
    )
    stream.add_argument(
        "--poll-interval",
        type=int,
        help="Interval to check local state in seconds",
    )
    stream.add_argument(
        "--topic-name",
        type=str,
        help="Topic to publish messages to",
    )
    stream.add_argument(
        "--source-name",
        type=str,
        help="Specify the name of this source of events. Defaults to system hostname.",
    )

    listen = subparsers.add_parser("listen")
    listen.set_defaults(execute=run_listen)
    listen.add_argument(
        "--subscription-name",
        type=str,
        help="Google subscription to recieve messages from",
    )
    listen.add_argument(
        "--no-blink1",
        action="store_true",
        help="Do not use blink(1) for user notifications",
    )

    args = parser.parse_args()
    args.execute(args)


if __name__ == "__main__":
    main()
