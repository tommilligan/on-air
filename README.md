# on-air

Show a warning light when a someone in the room is **on-air**, using [Google Cloud Pub/Sub](https://cloud.google.com/pubsub) and [blink(1)](https://blink1.thingm.com/).

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

## TODO List

- [x] Support multiple publisher to single subscriber flow
- [ ] Servicify clients using something like a `systemd` service
- [ ] One click/commandi install of python clients
- [ ] Better instructions/automated setup of GCP resources
- [ ] Configurable notification settings (color/blink count)
