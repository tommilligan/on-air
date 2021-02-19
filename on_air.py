#!/usr/bin/env python

import argparse
import glob
import json
import logging
import signal
import subprocess
import sys
import time
from typing import Callable, Dict, List, Optional, Tuple

import regex
from blink1.blink1 import Blink1
from google.auth import jwt
from google.cloud import pubsub

Payload = Dict[str, bool]

_log = logging.getLogger(__file__)

_LSOF_KNOWN_ERRORS = regex.compile(
    r"(?:Output information may be incomplete\.|WARNING: can't stat\(\) .* file system)"
)


class OnAirError(Exception):
    pass


class SystemError(OnAirError):
    pass


def lsof(pattern: str) -> List[str]:
    """
    List file owners owners for the specified glob pattern.
    """
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
    poll_interval: int, publish_payload: Callable[[Dict[str, bool]], None]
) -> None:

    # This will loop forever, so set a nice shutdown handler
    signal.signal(signal.SIGINT, shutdown)
    while True:
        audio_owners = lsof("/dev/snd/pcmC*")
        video_owners = lsof("/dev/video*")

        payload = {
            "audio": len(audio_owners) > 0,
            "video": len(video_owners) > 0,
        }
        publish_payload(payload)

        time.sleep(poll_interval)


def run_stream(args: argparse.Namespace) -> None:
    service_account_info = json.load(open(args.google_credential))
    audience = "https://pubsub.googleapis.com/google.pubsub.v1.Publisher"
    credentials = jwt.Credentials.from_service_account_info(
        service_account_info, audience=audience
    )
    publisher = pubsub.PublisherClient(credentials=credentials)
    topic_name = f"projects/{args.google_project_id}/topics/{args.google_topic}"

    def publish_payload(payload: Dict[str, bool]) -> None:
        data = json.dumps(payload)
        _log.info("Publishing message: '%s'", data)
        future = publisher.publish(topic_name, data.encode("utf-8"))
        future.result()

    poll_av_and_publish(
        poll_interval=args.poll_interval, publish_payload=publish_payload
    )


# Time for an interval of blinking, seconds
_BLINK_DURATION = 0.1
# Number of alerting blinks before solid color
_BLINK_REPEAT = 3

Rgb = Tuple[int, int, int]
_RGB_AUDIO_VIDEO = (255, 0, 0)
_RGB_VIDEO = (255, 182, 0)
_RGB_AUDIO = (255, 255, 0)
_RGB_CLEAR = (0, 255, 0)
_RGB_OFF = (0, 0, 0)


class DisplayState:
    _device: Optional[Blink1]
    _last_payload: Dict[str, bool]

    def __init__(self, device, last_payload: Payload):
        self._device = device
        self._last_payload = last_payload

    def __enter__(self, *args):
        return self

    def __exit__(self, *args):
        if self._device:
            self._device.off()

    def _solid(self, color: Rgb) -> None:
        _log.info("Color set: '%s'", color)
        if self._device:
            self._device.fade_to_rgb(0, *color)

    def _blink(self, color: Rgb) -> None:
        for _ in range(0, _BLINK_REPEAT):
            self._solid(color)
            time.sleep(_BLINK_DURATION)
            self._solid(_RGB_OFF)
            time.sleep(_BLINK_DURATION)

    def update(self, payload: Dict[str, bool]) -> None:
        if payload == self._last_payload:
            return

        self._last_payload = payload

        if payload["audio"] and payload["video"]:
            color = _RGB_AUDIO_VIDEO
        elif payload["audio"]:
            color = _RGB_AUDIO
        elif payload["video"]:
            color = _RGB_VIDEO
        else:
            # Once wer're clear, flash an all clear, then turn off
            color = _RGB_CLEAR
            self._blink(color)
            self._solid(color)
            time.sleep(3)
            self._solid(_RGB_OFF)
            return

        self._blink(color)
        self._solid(color)


_DEFAULT_MESSAGE = {"audio": False, "video": False}


def run_listen(args: argparse.Namespace) -> None:
    service_account_info = json.load(open(args.google_credential))
    audience = "https://pubsub.googleapis.com/google.pubsub.v1.Subscriber"
    credentials = jwt.Credentials.from_service_account_info(
        service_account_info, audience=audience
    )
    subscriber = pubsub.SubscriberClient(credentials=credentials)
    subscription_name = (
        f"projects/{args.google_project_id}/subscriptions/{args.google_subscription}"
    )

    if args.blink1:
        device = Blink1()
        device.off()
    else:
        device = None

    with DisplayState(device, _DEFAULT_MESSAGE) as display_state:

        def recieve_message(message):
            payload = message.data.decode("utf-8")
            _log.info("Recieved message: %s", payload)
            data = json.loads(payload)
            display_state.update(data)

            message.ack()

        # This will loop forever, so set a nice shutdown handler
        signal.signal(signal.SIGINT, shutdown)
        future = subscriber.subscribe(subscription_name, recieve_message)
        future.result()


def shutdown(signal_number, frame):
    _log.info(
        "Received signal '%s (%s)', shutting down",
        signal.strsignal(signal_number),
        signal_number,
    )
    sys.exit(1)


def main() -> None:
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(
        logging.Formatter("%(asctime)s|%(name)s|%(levelname)s|%(message)s")
    )
    root_logger.handlers = [stream_handler]

    parser = argparse.ArgumentParser(description="API Gateway Service")
    parser.add_argument(
        "--poll-interval",
        type=int,
        required=True,
        help="Interval to check local state in seconds",
    )
    parser.add_argument(
        "--google-project-id",
        type=str,
        required=True,
        help="Google project id to publish messages to",
    )
    parser.add_argument(
        "--google-credential",
        type=str,
        required=True,
        help="Google credential JWT file",
    )

    subparsers = parser.add_subparsers(required=True, dest="subcommand")
    stream = subparsers.add_parser("stream")
    stream.set_defaults(execute=run_stream)
    stream.add_argument(
        "--google-topic",
        type=str,
        required=True,
        help="Google topic to publish messages to",
    )

    listen = subparsers.add_parser("listen")
    listen.set_defaults(execute=run_listen)
    listen.add_argument(
        "--google-subscription",
        type=str,
        required=True,
        help="Google subscription to recieve messages from",
    )
    listen.add_argument(
        "--blink1",
        action="store_true",
        help="Use blink(1) to signal listened output",
    )

    args = parser.parse_args()
    args.execute(args)


if __name__ == "__main__":
    main()
