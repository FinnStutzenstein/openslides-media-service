import atexit
import base64
import json
from functools import wraps

from flask import Flask, Response, jsonify, request

from .auth import AUTH_HEADER, check_mediafile_id
from .config_handling import init_config
from .database import Database
from .exceptions import BadRequestError, HttpError, NotFoundError
from .logging import init_logging

app = Flask(__name__)
init_logging(app)
init_config(app)
database = Database(app)

app.logger.info("Started Media-Server")


@app.errorhandler(HttpError)
def handle_view_error(error):
    app.logger.error(
        f"Request to {request.path} resulted in {error.status_code}: "
        f"{error.message}"
    )
    res_content = {"success": False, "message": error.message}
    response = jsonify(res_content)
    response.status_code = error.status_code
    return response


def handle_general_errors(fn):
    @wraps(fn)
    def view(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            app.logger.exception(e)
            raise e

    return view


@app.route("/system/media/get/<int:mediafile_id>", strict_slashes=False)
@handle_general_errors
def serve(mediafile_id):
    # get mediafile id
    presenter_headers = dict(request.headers)
    del_keys = [key for key in presenter_headers if "content" in key]
    for key in del_keys:
        del presenter_headers[key]
    ok, filename, auth_header = check_mediafile_id(mediafile_id, app, presenter_headers)
    if not ok:
        raise NotFoundError()

    app.logger.debug(f'Filename for "{mediafile_id}" is {filename}')

    # Query file from db
    global database
    data, mimetype = database.get_mediafile(mediafile_id)

    # Send data (chunked)
    def chunked(size, source):
        for i in range(0, len(source), size):
            yield bytes(source[i : i + size])

    block_size = app.config["BLOCK_SIZE"]
    response = Response(chunked(block_size, data), mimetype=mimetype)
    response.headers["Content-Disposition"] = f'inline; filename="{filename}"'
    if auth_header:
        response.headers[AUTH_HEADER] = auth_header
    return response


@app.route("/internal/media/upload", methods=["POST"], strict_slashes=False)
@handle_general_errors
def upload():
    try:
        decoded = request.data.decode()
        dejson = json.loads(decoded)
    except Exception:
        raise BadRequestError("request.data is not json")
    try:
        media = base64.b64decode(dejson["file"].encode())
    except Exception:
        raise BadRequestError("cannot decode base64 file")
    try:
        media_id = int(dejson["id"])
        mimetype = dejson["mimetype"]
    except Exception:
        raise BadRequestError(
            f"The post request.data is not in right format: {request.data}"
        )
    app.logger.debug(f"to database media {media_id} {mimetype}")

    global database
    database.set_mediafile(media_id, media, mimetype)
    return {"success": True}, 200


def shutdown(database):
    app.logger.info("Stopping the server...")
    database.shutdown()
    app.logger.info("Done!")


atexit.register(shutdown, database)
