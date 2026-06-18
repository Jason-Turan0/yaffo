"""Load the Turan-Benchmark set.

Layout: ``<root>/<person>/<numberOfFaces>/<photo>``. The folder name is the ground
-truth face count for every photo inside it. The ``1`` folder doubles as that
person's reference (enrollment) photos (one face = that person); ``>1`` folders are
group photos that contain that person plus others.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
VIDEO_EXTS = {".mov", ".mp4", ".avi", ".mkv"}


@dataclass
class Photo:
    path: Path
    person: str
    expected_faces: int


@dataclass
class Person:
    name: str
    references: list[Photo] = field(default_factory=list)   # from the "1" folder
    groups: list[Photo] = field(default_factory=list)        # from ">1" folders


@dataclass
class Dataset:
    persons: dict[str, Person]
    photos: list[Photo]  # every photo, all persons/counts (for the detection benchmark)

    def reference_photos(self) -> list[Photo]:
        return [p for person in self.persons.values() for p in person.references]


def _is_image(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in IMAGE_EXTS and not p.name.startswith(".")


def load_dataset(root: Path) -> Dataset:
    persons: dict[str, Person] = {}
    photos: list[Photo] = []
    skipped_videos = 0

    for person_dir in sorted(d for d in root.iterdir() if d.is_dir()):
        person = Person(name=person_dir.name)
        for count_dir in sorted(d for d in person_dir.iterdir() if d.is_dir()):
            try:
                n = int(count_dir.name)
            except ValueError:
                continue  # not a face-count folder
            for f in sorted(count_dir.iterdir()):
                if f.suffix.lower() in VIDEO_EXTS:
                    skipped_videos += 1
                    continue
                if not _is_image(f):
                    continue
                photo = Photo(path=f, person=person.name, expected_faces=n)
                photos.append(photo)
                (person.references if n == 1 else person.groups).append(photo)
        persons[person.name] = person

    if skipped_videos:
        print(f"(skipped {skipped_videos} video files)")
    return Dataset(persons=persons, photos=photos)
