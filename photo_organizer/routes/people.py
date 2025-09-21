from flask import Flask, render_template, request, redirect, url_for
from photo_organizer.db.models import db, Person

def init_people_route(app: Flask):
    @app.route("/people")
    def people_list():
        people = Person.query.all()

        people_stats = []
        for person in people:
            num_faces = len(person.faces)
            # gather photos through faces
            photo_ids = {face.photo_id for face in person.faces if face.photo_id}
            num_photos = len(photo_ids)
            people_stats.append({
                "id": person.id,
                "name": person.name,
                "num_faces": num_faces,
                "num_photos": num_photos,
            })

        return render_template("people/list.html", people=people_stats)

    @app.route("/people/<int:person_id>/edit", methods=["GET", "POST"])
    def edit_person(person_id):
        person = Person.query.get_or_404(person_id)

        if request.method == "POST":
            new_name = request.form.get("name")
            if new_name:
                person.name = new_name
                db.session.commit()
            return redirect(url_for("people_list"))

        return render_template("people/edit.html", person=person)

    @app.route("/people/<int:person_id>/faces")
    def person_faces(person_id):
        person = Person.query.get_or_404(person_id)
        return render_template("people/faces.html", person=person, faces=person.faces)
