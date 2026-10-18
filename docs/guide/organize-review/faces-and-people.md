# Faces & People

Yaffo detects faces during indexing and lets you assign those faces to people.
After that, you can browse a person's faces or filter the library by people.

This page explains the concepts. For the hands-on, step-by-step workflow of
clearing the unassigned pile, see [Assigning Faces](assigning-faces.md).

## Explore the Face Review Page

<div class="annotated-screenshot" markdown="1">

![The Faces page showing a cluster of unassigned faces with assignment controls](assets/faces-and-people/faces-review.webp)

<div class="screenshot-annotation" data-x="97" data-y="4" data-region="24.5 2.5 73 6.5" markdown="1">

### Unassigned Count

How many unassigned faces are left to review.

</div>

<div class="screenshot-annotation" data-x="21" data-y="6.5" data-region="2.5 5.5 17.5 10.3" markdown="1">

### Assign or Ignore

Choose a person, then **Assign Selected**, or **Ignore Selected** for faces you don't want to track.

</div>

<div class="screenshot-annotation" data-x="21" data-y="21.5" data-region="2.5 17.5 17.5 5.5" markdown="1">

### Create Person

Create a person on the fly while assigning faces. [Read about people](#create-people).

</div>

<div class="screenshot-annotation" data-x="21" data-y="25" data-region="2.5 24.5 17.5 17" markdown="1">

### Keyboard Shortcuts

Number keys assign selected faces to the listed people; the gear chooses them.

</div>

<div class="screenshot-annotation" data-x="21" data-y="63" data-region="2.5 62.5 17.5 8.5" markdown="1">

### Grouping

**Group by** and **Similarity Threshold** control how faces are clustered. [Read about grouping](#understand-the-face-review-page).

</div>

<div class="screenshot-annotation" data-x="97" data-y="19" data-region="25.5 11.5 71 22" markdown="1">

### Cluster

A group of similar faces. Every face starts selected; click one to leave it out.

</div>

</div>

## Understand the Face Review Page

The **Faces** page shows unassigned faces. The goal is to empty that pile over
time.

Yaffo groups faces in two ways:

- **Group by People:** shows faces that look like people already in your library.
- **Group by Similarity:** clusters visually similar unassigned faces together.

The **Similarity Threshold** controls how strict grouping should be. Higher
values create tighter, cleaner groups. Lower values create larger, looser groups.

A practical first pass is:

1. Start with **Group by Similarity** at a high threshold.
2. Assign the biggest obvious clusters first.
3. Lower the threshold as the remaining groups get smaller.
4. Switch to **Group by People** after you have several known people.
5. Ignore low-quality faces that are blurry, partial, or not useful.

Refreshing between passes can help because Yaffo reclusters the remaining
unassigned faces. See [Assigning Faces](assigning-faces.md) for how to do all of
this in practice.

## Create People

Open **People** to create and manage people.

<div class="annotated-screenshot" markdown="1">

![The People page showing each person's details and assigned-face counts](assets/faces-and-people/people-list.webp)

<div class="screenshot-annotation" data-x="85.5" data-y="14" data-region="87.5 9.3 9.8 9.6" markdown="1">

### Add Person

Create a person with a name and optional gender and birthdate.

</div>

<div class="screenshot-annotation" data-x="12" data-y="48.5" data-region="2.3 46 7.8 5" markdown="1">

### Person

Click a name to review that person's faces. [Read about reviewing faces](#review-a-persons-faces).

</div>

<div class="screenshot-annotation" data-x="72" data-y="33" data-region="56 30 15.5 68" markdown="1">

### Face and Photo Counts

How many faces are assigned to each person, and how many photos they appear in.

</div>

<div class="screenshot-annotation" data-x="90.5" data-y="48.5" data-region="82.3 46.5 6.8 4.5" markdown="1">

### Edit and Delete

Change a person's details, or delete the person.

</div>

</div>

Each person can have:

- a name;
- an optional gender;
- an optional birthdate.

A birthdate helps Yaffo compare a person's faces from similar life stages. Gender
is used by the gallery's gender filter. Neither field is required.

You can also create a person on the fly while assigning faces, using the
**Create Person** box in the Faces sidebar.

## Review a Person's Faces

Click a person in the **People** table to open that person's face page. From
there, you can:

- see all faces assigned to the person;
- filter by similarity;
- select incorrect faces;
- click **Remove Selected** to remove wrong assignments.

Removing a face from a person does not delete the original photo. It only removes
that face assignment.

## Automatic Assignment

The built-in **Auto-assign faces** automation runs when a photo is indexed. It
compares each detected face with your known people and assigns a face when one
person clears the configured match threshold.

By default, a face that strongly matches more than one person stays unassigned
for you to review. You can instead enable **Assign when multiple people match**
to choose the highest-scoring match. A higher threshold makes fewer, more
confident assignments.

Open **Library** → **Automations** → **Auto-assign faces** to enable or disable
the automation and change those settings. Review automatic results periodically,
especially while each person has only a few examples.
