# on-air

Show a warning light when someone in the room is **on-air**, using [Google Cloud Pub/Sub](https://cloud.google.com/pubsub) and [blink(1)](https://blink1.thingm.com/).

## Getting started

### Google Cloud project

You will need to create a [Google Cloud Pub/Sub](https://cloud.google.com/pubsub) resource to act as a message queue.

You will also need to provision a service accounts for each device in the system:

- one or more publisher credentials, to write events
- one subscriber credential, to read events

This is currently outside the scope of this readme.

### Local clients

The clients are both contained in a single python script. Some third party dependencies are required.

```bash
# Clone this repo
git clone https://github.com/tommilligan/on-air
cd on-air

# Instll third-party deps
pip install -r requirements.txt
```

On devices with hardware to monitor, start streaming events up to the cloud:

```bash
# The variables you will need to fill in from your GCP project
GOOGLE_PROJECT_ID=google-project-012345
GOOGLE_CREDENTIAL=publisher-credential-file.json
TOPIC_NAME=topic-name

# This will run forever, watching for changes. Use Ctrl+C to exit.
./on_air.py  \
  --google-project-id "$GOOGLE_PROJECT_ID"  \
  --google-credential "$GOOGLE_CREDENTIAL"  \
  stream  \
  --poll-interval 5  \
  --topic-name "$TOPIC_NAME"
```

On a device with a [blink(1)](https://blink1.thingm.com/) indicator plugged in, start listening for events:

```bash
# The variables you will need to fill in from your GCP project
GOOGLE_PROJECT_ID=google-project-012345
GOOGLE_CREDENTIAL=subscriber-credential-file.json
SUBSCRIPTION_NAME=subscription-name

# This will run forever, watching for changes. Use Ctrl+C to exit.
./on_air.py  \
  --google-project-id "$GOOGLE_PROJECT_ID"  \
  --google-credential "$GOOGLE_CREDENTIAL"  \
  listen  \
  --subscription-name "$SUBSCRIPTION_NAME"
```

## blink1 setup

For a linux machine, you will need to add udev rules for the device. A copy is in this repo:

```bash
sudo cp 51-blink1.rules /etc/udev/rules.d/
sudo udevadm control --reload
```

## Raspberry Pi Further Setup

Some additional steps and dependencies are needed for Raspberry Pi:

```bash
sudo apt-get install  \
  python3-venv
cd ~
mkdir on-air
cd on-air
python3 -m venv env
```

### Systemd Services

Example service files are included for both listener (rpi) and streamer.

### Log rotation

You should set up log rotation like:

```conf
# /etc/logrotate.d/on-air-listen

/home/pi/on-air/on-air.log {
  daily
  missingok
  rotate 4
  compress
  copytruncate
}
```

## Architecture

The architecture used is a multiple-publisher single-consumer pub/sub flow:

```
                 +----------------+
                 |                |
                 |  PubSub Queue  |
                 |                |
                 +--^----------+--+
                    |          |
                    |          |
         +----------+--+    +--v-----------+
         |             |    |              |
         |  Publisher  |    |  Subscriber  |
         |             |    |              |
         +----------^--+    +--+-----------+
                    |          |
                    |          |
+-------------------+-+      +-v---------------+
|                     |      |                 |
|  Camera/Microphone  |      |  LED Indicator  |
|                     |      |                 |
+---------------------+      +-----------------+
```

Changes to hardware usage on the publisher device are polled for using [`lsof`](https://man7.org/linux/man-pages/man8/lsof.8.html) to watch the devices mounted in [`/dev`](https://tldp.org/LDP/Linux-Filesystem-Hierarchy/html/dev.html).
When a change is detected, a message is published to a topic in [Google Cloud Pub/Sub](https://cloud.google.com/pubsub).

A subscriber interested in changes listens for messages on a linked topic using a [`google` gRPC streaming client](https://github.com/googleapis/python-pubsub).
On recieving a message, the subscriber caches messages by source. It then computes an additive state for all publishers, and triggers a user notification via the [blink(1)](https://blink1.thingm.com/) indicator if applicable.

## Requirements

- Low latency: less than 10 seconds latency from change to notification
  - The limiting factor is how fast the publisher polls for hardware changes
  - If a very short poll time is chosen, there may be some interesting effects as LED updates overlap
- Low throughput: stay under the GCP free tier
  - To reduce messaging volume, the publisher caches local state and only publishes a new message on change
  - Data payload size could be reduced by encoding the payload using something like protobuf (currently JSON)
- Fail open: if state is unknown, default to indicator off
  - On subscriber crash or restart, the indicator is reset

## Configuration

For a streamer the easiest installation is to install the debian package. This will automatically install a service daemon to stream events in the background.

Additional configuration is required for this method:

- Create a system directory for configuration: `sudo mkdir /etc/on-air`
- Move your GCP JWT credential here: `sudo mv google-credential.json /etc/on-air/google-credential.json`
- Set up a config file at `/etc/on-air/on-air.json`. Example contents:

```json
{
  "google_project_id": "google-project-012345",
  "google_credential": "/etc/on-air/google-credential.json",
  "poll_interval": 5,
  "topic_name": "topic-name"
}
```

## TODO List

- [x] Support multiple publisher to single subscriber flow
- [x] Servicify clients using something like a `systemd` service
- [x] One click/command install of python clients
- [ ] Better instructions/automated setup of GCP resources
  - [ ] Subscribers create their own subscription topic
- [ ] Configurable notification settings (color/blink count)
  - [x] Load settings from config file
