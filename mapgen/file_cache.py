import sqlite3
import os
import pickle


class FileCache:
    def __init__(self):
        script_dir = os.path.dirname(__file__)
        cache_dir = os.path.join(script_dir, 'cache')
        os.makedirs(cache_dir, exist_ok = True)
        self.cache_file = os.path.join(cache_dir, "global_session")
        with sqlite3.connect(self.cache_file) as cache:
            cur = cache.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS cache(key,value, UNIQUE(key))")

    def get(self, key, default = None):
        try:
            return self.__getitem__(key)
        except KeyError:
            return default

    def __getitem__(self, key):
        with sqlite3.connect(self.cache_file) as cache:
            cur = cache.cursor()
            cur.execute("SELECT value FROM cache WHERE key=?", (key, ))
            value = cur.fetchone()
            if value is None:
                raise KeyError(key)

        return pickle.loads(value[0])

    def __setitem__(self, key, value):
        value = pickle.dumps(value)
        with sqlite3.connect(self.cache_file) as cache:
            cur = cache.cursor()
            try:
                cur.execute("INSERT INTO cache (key,value) VALUES (?,?)",
                            (key, value))
            except sqlite3.IntegrityError:
                cur.execute("UPDATE cache SET value=? WHERE key=?",
                            (value, key))
            cache.commit()

    def __delitem__(self, key):
        with sqlite3.connect(self.cache_file) as cache:
            cur = cache.cursor()
            cur.execute("DELETE FROM cache WHERE key=?", (key, ))
            cache.commit()
