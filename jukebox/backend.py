"""
Jukebox audio file serving backend application
Provides a CLI and a Pyro remote API.

Written by Irmen de Jong (irmen@razorvine.net) - License: MIT open-source.
"""
import sys
import cmd
import shlex
import threading
import Pyro4
from .musicfiledb import MusicFileDatabase


BACKEND_PORT = 39776


class JukeboxBackendRemoting:
    def __init__(self):
        self.mdb = MusicFileDatabase(silent=True, scan_changes=False)

    def __del__(self):
        if self.mdb:
            self.mdb.close()
            self.mdb = None

    @Pyro4.expose
    def track(self, track_id=None, hashcode=None):
        track = self.mdb.get_track(track_id, hashcode)
        return self.track2dict(track)

    @Pyro4.expose
    @property
    def num_tracks(self):
        return self.mdb.num_tracks()

    @Pyro4.expose
    @property
    def total_playtime(self):
        return self.mdb.total_playtime()

    @Pyro4.expose
    def query(self, title=None, artist=None, album=None, year=None, genre=None):
        max_results = 200
        return [self.track2dict(t) for t in self.mdb.query(title, artist, album, year, genre, result_limit=max_results)]

    @Pyro4.expose
    def get_file(self, track_id=None, hashcode=None):
        track = self.mdb.get_track(track_id, hashcode)
        with open(track.location, "rb") as f:
            return f.read()

    @Pyro4.expose
    def get_file_chunks(self, track_id=None, hashcode=None):
        track = self.mdb.get_track(track_id, hashcode)
        with open(track.location, "rb") as f:
            while True:
                chunk = f.read(128 * 1024)
                if not chunk:
                    break
                yield chunk

    def track2dict(self, track):
        result = vars(track)
        result["hash"] = track.hash
        return result


class JukeboxBackendCli(cmd.Cmd):
    def __init__(self, mdb, pyro_uri):
        super().__init__()
        self.mdb = mdb
        self.pyro_uri = pyro_uri
        print("Number of tracks in database:", self.mdb.num_tracks())
        print("Pyro connection uri: ", self.pyro_uri)

    def do_reload(self, args):
        """Reload the whole database."""
        if self.mdb:
            self.mdb.close()
        self.mdb = MusicFileDatabase(scan_changes=False)
        print("Number of tracks in database:", self.mdb.num_tracks())

    def do_quit(self, args):
        """Exits the program."""
        print("Bye.", args)
        return True

    def do_stats(self, args):
        """Prints some stats such as the number of tracks in the database."""
        print("Number of tracks in database: ", self.mdb.num_tracks())
        print("Total play time: ", self.mdb.total_playtime())
        print("Pyro connection uri: ", self.pyro_uri)

    def do_query(self, args):
        """Perform a query on the database. Arguments are:  field=search-value [...]"""
        if not args:
            print("Give at least one field filter.")
            return
        filters = shlex.split(args)
        try:
            filters = {f: v for f, v in (f.split('=') for f in filters)}
        except ValueError:
            print("Query arguments syntax error. Try help for this command.")
            return
        try:
            results = self.mdb.query(**filters)
        except TypeError:
            import inspect
            fields = list(inspect.signature(self.mdb.query).parameters)
            print("Invalid filter field. Valid fields are:", fields)
            return
        except Exception as x:
            print("ERROR:", x)
            return
        print("Found {:d} results. Showing max 6:".format(len(results)))
        for track in results[:6]:
            self.print_track(track, full=False)
            print()

    def print_track(self, track, full=False):
        print("Track #{:d}".format(track.id))
        print("     title:", track.title or "")
        print("    artist:", track.artist or "")
        print("     album:", track.album or "")
        print("      year:", track.year or "")
        print("     genre:", track.genre or "")
        print("  duration:", track.duration)
        if full:
            print("  modified:", track.modified)
            print("      hash:", track.hash)
            print("  location:", track.location)

    def do_path(self, path):
        """Reads the music files or iTunes library in the given path."""
        if not path:
            print("Give a path to scan for music files or iTunes library.")
            return
        self.mdb.update_path(path)

    def do_rescan(self, args):
        """Rescan the files in the database to see if there were changes."""
        self.mdb.scan_changes()

    def do_track(self, track_id):
        """Get all information for a single track by id or hash."""
        if not track_id:
            print("Give track id or hash.")
            return
        try:
            track = self.mdb.get_track(track_id=track_id)
        except LookupError:
            try:
                track = self.mdb.get_track(hashcode=track_id)
            except LookupError:
                print("Track not found.")
                return
        self.print_track(track, full=True)


class Backend:
    def __init__(self, scan=True):
        self.mdb = MusicFileDatabase(scan_changes=scan)
        self.pyro_daemon = Pyro4.Daemon(host="", port=BACKEND_PORT)
        self.pyro_uri = self.pyro_daemon.register(JukeboxBackendRemoting, "jukebox.backend")
        self.cli = JukeboxBackendCli(self.mdb, self.pyro_uri)

    def run(self):
        pyro_thread = threading.Thread(target=self.pyro_daemon.requestLoop)
        pyro_thread.start()
        try:
            self.cli.cmdloop("Jukebox backend. Enter commands or 'help' for help.")
        except KeyboardInterrupt:
            print("\n<BREAK>")
        except Exception:
            print("\nAn error has occurred:")
            import traceback
            traceback.print_exc()
        print("Jukebox backend is stopping.")
        self.mdb.close()
        self.pyro_daemon.shutdown()
        pyro_thread.join()


if __name__ == "__main__":
    scan_changes = True
    if len(sys.argv) > 1:
        if sys.argv[1] in ("-h", "--help"):
            print("Arguments:")
            print("  -noscan   = don't scan disk for changes")
            raise SystemExit
        if sys.argv[1] == "-noscan":
            scan_changes = False
    backend = Backend(scan_changes)
    backend.run()
