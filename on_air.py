#!/usr/bin/env python

import argparse
import glob
import json
import logging
import subprocess
import time
from typing import Any, Callable, Dict, List

import regex
from google.cloud import pubsub_v1

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


def run(poll_interval: int, publish_message: Callable[[Dict[str, Any]], None]) -> None:
    while True:
        audio_owners = lsof("/dev/snd/pcmC*")
        video_owners = lsof("/dev/video*")

        message = {
            "audio": len(audio_owners) > 0,
            "video": len(video_owners) > 0,
        }
        publish_message(message)

        time.sleep(poll_interval)


def main() -> None:

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(
        logging.Formatter("%(asctime)s|%(name)s|%(levelname)s|%(message)s")
    )
    root_logger.handlers = [stream_handler]

    parser = argparse.ArgumentParser(description="API Gateway Service")
    arg = parser.add_argument
    arg(
        "--poll-interval",
        type=int,
        required=True,
        help="Interval to check local state in seconds",
    )
    arg(
        "--google-project-id",
        type=str,
        required=True,
        help="Google project id to publish messages to",
    )
    arg(
        "--google-topic",
        type=str,
        required=True,
        help="Google topic to publish messages to",
    )
    args = parser.parse_args()

    publisher = pubsub_v1.PublisherClient()
    topic_name = f"projects/{args.google_project_id}/topics/{args.google_topic}"
    # publisher.create_topic(topic_name)

    def publish_message(message: Dict[str, Any]) -> None:
        future = publisher.publish(topic_name, json.dumps(message).encode("utf-8"))
        future.result()

    run(poll_interval=args.poll_interval, publish_message=publish_message)


if __name__ == "__main__":
    main()
