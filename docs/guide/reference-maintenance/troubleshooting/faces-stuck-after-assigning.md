# Faces Stuck After Assigning

If assigned faces keep appearing in the review pile, or face processing never
finishes, start by checking the job and face status. These symptoms can have
different causes.

1. Open **Faces** and refresh the page. Check whether the faces are still
   unassigned or whether the same person has already been linked to them.
2. Check the related indexing or face job for progress and errors. If a job never
   starts, restart Yaffo so its background task host can resume queued work.
3. If the job has finished but faces remain stuck, use **Ask Yaffo** to run a
   health check. It can identify faces linked to a person but still marked
   unassigned, or faces left in **PROCESSING** with no queued face task.
4. When the assistant proposes a **Repair faces** change, review the affected
   count and approve its card. The repair corrects inconsistent face statuses;
   it does not choose a person for an unassigned face.

For ordinary unassigned faces, continue with the [Assigning Faces](../../organize-review/assigning-faces.md)
workflow. If automatic assignment is enabled, review its threshold and recent
runs under **Utilities** → **Automations** → **Auto-assign faces**.
