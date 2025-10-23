# main.py
from yaffo.background_tasks.config import huey  # import the "background_tasks" object.
from yaffo.background_tasks.tasks import index_photo_task  # import any background_tasks / decorated functions

if __name__ == '__main__':
    result = index_photo_task('', [])
    print('1 + 2 = %s' % result.get(blocking=True))