# YAFFO

YAFFO (Yet Another Family Foto Organizer)  is a photo organization tool that uses EXIF metadata, face recognition,
duplicate detection, and offline ML classification to organize and index media.

## Motivation

I wanted a simple thing: find pictures of my kids in my own photo library. The
tools I tried made that harder than it should be. Amazon Photos and similar
services put my pictures inside a walled garden and didn't provide any controls how 
people labeling was performed. Amazon Photos still can't tell the difference between
my two kids and has no practical way to update the labels it attached.

After being unhappy with the available options, I built the tool I wanted for
this job: local-first, practical, searchable, and shaped around organizing a real
personal photo collection. The UI is built around working in batches, because
photo organization is rarely one photo at a time. The goal is to move quickly
through groups of related work: review faces, assign people, clean up
duplicates, and apply organization changes with as little repetitive clicking as
possible.

Yaffo is also designed to maximize the amount of work the system can do on its
own through automatic indexing, metadata extraction, face recognition, duplicate
detection, and organization rules. The human should spend time on decisions that
actually need judgment, not on mechanical sorting. Yaffo is open source because
that same problem is not unique to me, and I hope it is useful to others who
want more control over their photo library.