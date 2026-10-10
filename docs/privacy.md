# Privacy Policy

_Last updated: October 9, 2026_

Yaffo is a desktop application that runs on your own computer. It has no
user accounts and no Yaffo-operated servers that store your photos or your
data. This policy explains what Yaffo does with your data, including data
from the online services you choose to connect it to.

## Your photos and library

Yaffo reads and indexes the photo folders you add to it. The index (metadata,
faces, labels, albums, and settings) is stored in a database on your
computer. Face recognition and auto-classification run on your computer. None
of this is sent to Yaffo's developers or to anyone else unless you set up a
feature that sends it, such as the ones described below.

## Maps, place names, and model downloads

- **Maps.** The map view loads map tiles from OpenStreetMap, which reveals
  the map area you're viewing to OpenStreetMap's tile servers.
- **Place names.** Looking up a place name, or the place for a photo's GPS
  coordinates, sends that text or those coordinates to OpenStreetMap's
  Nominatim service.
- **Model downloads.** On first use, Yaffo downloads its recognition models
  from public hosts such as Hugging Face.

## AI assistant

If you configure an AI model provider (for example Anthropic or OpenAI) for
Ask Yaffo, the AI page builder, or automations, the prompts you write and the
library details needed to answer them are sent to that provider, under that
provider's own terms. Yaffo works without one.

## Sharing between devices

When you pair devices and share albums, photos travel between your devices,
end-to-end encrypted. The relay at `hub.yaffo.app` forwards encrypted traffic
and can't read it.

## Connected services (sync)

You can connect Yaffo to online photo and storage services, such as Microsoft
OneDrive and Google Photos, to import photos or to keep an album copied
there. When you do:

- **What Yaffo accesses.** Only what the sync you set up needs:
  - for OneDrive, the files in the folders you choose;
  - for Google Photos, the albums and photos Yaffo itself created. Google
    doesn't let Yaffo see the rest of your Google Photos library.
- **Where it's stored.** The sign-in tokens are stored in your operating
  system's keychain. Remote IDs (for example, which uploaded item matches
  which photo) are stored in Yaffo's database on your computer. Nothing is
  stored by Yaffo's developers.
- **How it's used.** Only to perform the syncs you configured: uploading,
  downloading, and keeping albums in step. It isn't used for advertising,
  sold, transferred to anyone else, or read by people.
- **How to stop.** Remove the account in Yaffo. You can also revoke access
  from the service:
  - Google: [Third-party connections](https://myaccount.google.com/connections)
    in your Google Account.
  - Microsoft: [Apps and services](https://account.live.com/consent/Manage)
    in your Microsoft account.

### Google user data

Yaffo's use and transfer to any other app of information received from
Google APIs will adhere to the
[Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
including the Limited Use requirements.

## Usage data

Yaffo doesn't collect analytics or telemetry.

## Changes and contact

Changes to this policy are published on this page with a new date. Questions:
open an issue at
[github.com/Jason-Turan0/yaffo](https://github.com/Jason-Turan0/yaffo/issues)
or email turan.jason@gmail.com.
