#!/bin/bash

# Activate virtual environment if needed
if [ -f "activate_venv.sh" ]; then
    source activate_venv.sh
fi

huey_consumer.py photo_organizer.background_tasks.main.huey -w 8 -k process