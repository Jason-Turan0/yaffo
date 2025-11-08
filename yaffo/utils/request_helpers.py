from flask import Request
boolean_map = {'true': True, 'false': False}

def parse_boolean_from_form(request: Request, name: str, default: bool) -> bool:
    value = request.form.get(name, default)
    if isinstance(value, str) and value in boolean_map.keys():
        return boolean_map.get(value.lower())
    return default
