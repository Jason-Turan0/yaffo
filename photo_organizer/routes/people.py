from flask import Flask, render_template
from photo_organizer.db.models import Person

def init_people_route(app: Flask):
    @app.route("/people")
    def list_people():
        people = Person.query.all()
        return render_template("people.html", people=people)

    @app.route("/people/new", methods=["GET", "POST"])
    def new_person():
        return render_template("people.html", people=people)