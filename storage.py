import json
import os
import threading
from datetime import datetime


DATA_DIR = "data"
_JSON_LOCKS = {}
_JSON_LOCKS_GUARD = threading.Lock()


def get_json_lock(path):
    normalized = os.path.abspath(path)
    with _JSON_LOCKS_GUARD:
        if normalized not in _JSON_LOCKS:
            _JSON_LOCKS[normalized] = threading.RLock()
        return _JSON_LOCKS[normalized]


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def data_path(filename):
    ensure_data_dir()
    return os.path.join(DATA_DIR, filename)


def backup_corrupt_file(path):
    if not os.path.exists(path):
        return None

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_path = f"{path}.corrupt-{timestamp}.bak"
    try:
        os.replace(path, backup_path)
        print(f"Backed up corrupted JSON file: {backup_path}")
        return backup_path
    except OSError as error:
        if getattr(error, "winerror", None) == 32:
            print("Could not back up corrupted JSON file because it is currently locked. Recreating default data.")
        else:
            print(f"Could not back up corrupted JSON file {path}: {error}")
        return None


def read_json(path, default_data, recreate_on_error=False):
    lock = get_json_lock(path)
    with lock:
        def recreate_default():
            if not recreate_on_error:
                return
            try:
                write_json(path, default_data)
            except OSError as error:
                print(f"Could not recreate {path}: {error}")

        if not os.path.exists(path):
            recreate_default()
            return default_data

        try:
            with open(path, "r", encoding="utf-8") as file:
                return json.load(file)
        except json.JSONDecodeError:
            backup_corrupt_file(path)
            recreate_default()
            return default_data
        except OSError as error:
            print(f"Could not read {path}: {error}")
            recreate_default()
            return default_data


def write_json(path, data):
    lock = get_json_lock(path)
    with lock:
        ensure_data_dir()
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        temp_path = f"{path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
            file.flush()
            os.fsync(file.fileno())

        os.replace(temp_path, path)


def migrate_json_files_to_data(filenames):
    """Copy root JSON files into data/ once, then future writes can use data/."""
    ensure_data_dir()
    for filename in filenames:
        old_path = filename
        new_path = data_path(filename)
        if not os.path.exists(old_path) or os.path.exists(new_path):
            continue

        try:
            data = read_json(old_path, None)
            if data is not None:
                write_json(new_path, data)
                print(f"Migrated {filename} to data/{filename}.")
        except OSError as error:
            print(f"Could not migrate {filename} to data/: {error}")
