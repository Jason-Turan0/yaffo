# Automations

Automations are background behaviors that run on their own — either on a schedule
or in response to something happening, such as a photo being indexed. They are how
Yaffo keeps a library tidy without you clicking through the same steps by hand.

Open **Library** → **Automations**.

![The Automations page with the system and custom list, and a selected automation's details and run history](assets/automations/automations-list.webp)

The sidebar groups automations under **System**, **Syncs**, and **Custom**.
Custom photo services appear under **Services** after you add one. Select an
automation to see its setup and run history. An **ON** badge marks enabled ones.

**New automation** offers four choices:

| Choice | Use it for |
|---|---|
| **Custom** | A rule written from your description, such as tagging matching photos. |
| **Import from…** | Copying photos into your library from a folder, photo service, or paired device. |
| **Send to…** | Keeping selected library photos copied to a folder or photo service. |
| **Add a new service…** | Building a reusable import or send integration for another photo service. |

Preset syncs are configured with forms. The model and key under
[Settings → AI Generation](../reference-maintenance/settings.md#ai-generation)
are used when building custom automations, selection rules, and services.

## System Automations

System automations ship with Yaffo and cover its routine upkeep. They can be
enabled, disabled, and tuned, but not deleted. They include:

- **Assign location name** — names a photo's location from GPS when it is indexed.
- **Auto-assign faces** — assigns new faces to people that already match.
- **Classify labels** — runs label classification on new photos.
- **Duplicate scan** — looks for duplicate photos.
- **Export photo tag** — writes selected tags back to photo metadata.
- **File sync** — keeps the index in step with your media folders.
- **Geotag from neighbors** — fills in missing GPS from nearby photos taken close
  in time.

Select a system automation to turn its switch on or off or **Run…** it on demand.
Open **When** to edit its triggers and **Configuration** to adjust its settings,
such as a similarity threshold.

## Create a Custom Automation

Click **New automation**, choose **Custom**, and give it a name. Describe what it
should do in plain language — *"tag every photo imported in the last day as New
arrival"* — and the assistant writes the automation for you.

While you build it, your work is a draft:

- **Publish draft** makes the draft the live version that actually runs.
- **Discard** throws the draft away.

Use **Edit details** to change the name or description, and **Delete** to remove a
custom automation.

## Import and Export Photos

Follow [Importing Photos](importing.md) to copy photos from a folder, photo
service, or paired device into your library. Follow [Exporting Photos](exporting.md)
to send selected library photos to a folder or photo service. Both walkthroughs
cover setup, previews, enabling the sync, and monitoring transfers.

Each sync has one direction. Creating an import and a send sync for the same
service does not make a two-way mirror.

## Add a Photo Service

For a service outside the built-in choices, follow [Creating a Photo Service](photo-services.md).
The walkthrough covers building, connecting, testing, and publishing a reusable
service before creating an import or send sync.

## Test Before Publishing

Before you turn a custom automation loose on your whole library, try it on a small
part of it. Click **Test…** in the editor and pick a file or folder.

The test is a safe dry-run:

- It runs the automation against only the indexed photos at or under the path you
  chose.
- It shows what the automation *would* do — such as which photos it matched —
  without actually changing anything.
- Nothing is written and no run is recorded in the automation's history.

![A custom automation editor showing its conversation, published code, and safe dry-run result](assets/automations/automation-test.webp)

You can test either the working **draft** or the currently **published** code, so
you can check a change before publishing it. Once the results look right, use
**Publish draft** to make it live.

## Triggers: When an Automation Runs

Open **When** to inspect and edit an automation's triggers. You can add one or
several:

- **Schedule** — run on a repeating clock, set with a cron expression (for
  example, every night at 2 AM). Yaffo validates the expression as you type.
- **Event** — run when something happens in Yaffo, such as a photo being indexed.

![The trigger editor with a new hourly schedule ready to save](assets/automations/automation-triggers.webp)

New syncs start with a nightly schedule. Album send syncs also react to membership
changes and library deletions; bursts of edits are combined into one run. Paired
device imports also react when their device comes online. An automation with no
triggers runs only when you start it manually.

## Run and Review

Use **Run…** for system and custom automations, or **Run now** for an enabled sync.
**Test** on a sync previews the current setup without copying or removing files;
its preview has a pager when more photos are available.

Every run — scheduled, event-driven, or manual — is recorded under **Run
history**, with a status such as **COMPLETED** and a timestamp. If an automation
misbehaves, check its run history first to see what happened and when. A run that
failed or finished with errors has an **Ask Yaffo** icon at the end of its row;
it opens the [assistant](../start-here/ask-yaffo.md) with that run attached.

For a sync, expand **Items** to search individual photos, filter by status, and
read the reason for a failure or protected copy. Use **Retry failed** after
fixing the problem. If an account needs sign-in again, completing that sign-in
also fixes the other syncs using the same account. Turn a sync's switch off to
pause automatic runs.

## Related

Several system automations power features documented elsewhere:

- [Labels and Auto-Classification](../organize-review/labels.md) — Classify labels.
- [Locations & Map](../organize-review/locations.md) — Assign location name and
  Geotag from neighbors.
- [Finding Duplicates](../organize-review/duplicates.md) — Duplicate scan.

See the [Settings Reference](../reference-maintenance/settings.md) for media
directory paths, AI model selection, and API keys.
