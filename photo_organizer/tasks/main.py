# main.py
from photo_organizer.tasks.config import huey  # import the "huey" object.
from photo_organizer.tasks.tasks import add  # import any tasks / decorated functions


if __name__ == '__main__':
    result = add(1, 2)
    print('1 + 2 = %s' % result.get(blocking=True))