import sys
import hashlib
import json
import os
import queue
import urllib.request
import urllib.error
import re
import shutil
import subprocess
import unicodedata
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tkinter import *
from tkinter import ttk, filedialog, messagebox, PhotoImage
import tkinter as tk

if sys.platform != 'win32':
    import fcntl

def resource_path(*parts):
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base_path.joinpath(*parts)

try:
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4
    from mutagen.flac import FLAC
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False

DEFAULT_DEST = 'D:\\' if sys.platform == 'win32' else '/Volumes/NO NAME'
MEDIA_EXTS = {'.mp3', '.m4a', '.aac', '.flac', '.wav', '.aiff', '.ogg', '.wma', '.mp4', '.m4v', '.mov'}
MANIFEST_FILE = '.NUGLIB'
LOCAL_CONFIG = Path.home() / '.nugget_sync_config.json'
READ_WORKERS = 16
WRITE_QUEUE_MAX = 30
ART_WORKERS = 16
ART_SIZE = 500
UI_FONT = 'Segoe UI' if sys.platform == 'win32' else 'System'
MONO_FONT = 'Consolas' if sys.platform == 'win32' else 'Menlo'

JXA_TRACKS = """
var music = Application('Music');
var ft    = music.libraryPlaylists[0].fileTracks;
var count = ft.length;

var ids          = ft.persistentID();
var names        = ft.name();
var artists      = ft.artist();
var albumArtists = ft.albumArtist();
var albums       = ft.album();
var genres       = ft.genre();
var kinds        = ft.kind();
var trackNums    = ft.trackNumber();
var discNums     = ft.discNumber();
var discCounts   = ft.discCount();
var locations    = ft.location();
var modDates     = ft.modificationDate();
var bitRates     = ft.bitRate();
var bpms         = ft.bpm();
var years        = ft.year();
var composers    = ft.composer();
var loved        = [];
try { loved = ft.loved(); } catch(e) {
    try { loved = ft.favorited(); } catch(e2) {}
}

var result =[];
for (var i = 0; i < count; i++) {
    try {
        var loc = locations[i];
        var locStr = (loc && loc.toString) ? loc.toString() : null;
        if (!locStr || locStr === 'null') continue;
        
        var md = modDates[i];
        var mdate = (md && md.getTime) ? md.getTime() : 0;

        result.push({
            id:          ids[i]          || '',
            name:        names[i]        || '',
            artist:      artists[i]      || '',
            albumArtist: albumArtists[i] || '',
            album:       albums[i]       || '',
            genre:       genres[i]       || '',
            kind:        kinds[i]        || '',
            trackNumber: trackNums[i]    || 0,
            discNumber:  discNums[i]     || 1,
            discCount:   discCounts[i]   || 1,
            mdate:       mdate,
            bitRate:     bitRates[i]     || 0,
            bpm:         bpms[i]         || 0,
            year:        years[i]        || 0,
            composer:    composers[i]    || '',
            location:    locStr,
            loved:       loved[i]        || false
        });
    } catch(e) {}
}
JSON.stringify(result);
"""

JXA_PLAYLISTS = """
var music  = Application('Music');
var pls    = music.playlists();
var result =[];
for (var i = 0; i < pls.length; i++) {
    var p = pls[i];
    try {
        if (p.specialKind() !== 'none') continue;
        var name = p.name();
        var isSmart = false;
        try { isSmart = p.smart(); } catch(e) {}
        var ft   = p.fileTracks;
        var ids  = ft.persistentID();
        if (ids.length > 0) {
            result.push({ name: name, tracks: ids, smart: isSmart });
        }
    } catch(e) {}
}
JSON.stringify(result);
"""

JXA_UPDATE_PLAYLIST = """
function run(argv) {
    var input = JSON.parse(argv[0]);
    var music = Application('Music');
    
    if (input.is_favorites) {
        var libTracks = music.libraryPlaylists[0].fileTracks;
        input.add.forEach(function(id) {
            try { libTracks.whose({persistentID: id})[0].loved = true; } catch(e) {
                try { libTracks.whose({persistentID: id})[0].favorited = true; } catch(e2) {}
            }
        });
        input.remove.forEach(function(id) {
            try { libTracks.whose({persistentID: id})[0].loved = false; } catch(e) {
                try { libTracks.whose({persistentID: id})[0].favorited = false; } catch(e2) {}
            }
        });
    } else {
        var pl = music.playlists.whose({name: input.name})[0];
        if (!pl) return;
        var libTracks = music.libraryPlaylists[0].fileTracks;
        
        if (input.remove && input.remove.length > 0) {
            var plTracks = pl.tracks;
            for (var i = plTracks.length - 1; i >= 0; i--) {
                var t = plTracks[i];
                if (input.remove.includes(t.persistentID())) {
                    try { t.delete(); } catch(e) {}
                }
            }
        }
        if (input.add && input.add.length > 0) {
            input.add.forEach(function(id) {
                try {
                    var t = libTracks.whose({persistentID: id})[0];
                    t.duplicate({to: pl});
                } catch(e) {}
            });
        }
    }
}
"""

JXA_IMPORT = """
function run(argv) {
    var paths = JSON.parse(argv[0]);
    var music = Application('Music');
    paths.forEach(function(p) {
        try { music.add(Path(p)); } catch(e) {}
    });
}
"""

JXA_UPDATE_COUNTS = """
function run(argv) {
    var input = JSON.parse(argv[0]);
    var music = Application('Music');
    var tracks = music.libraryPlaylists[0].fileTracks();
    for (var i = 0; i < tracks.length; i++) {
        var t = tracks[i];
        var id = t.persistentID();
        if (input.plays && input.plays[id]) {
            t.playedCount = (t.playedCount() || 0) + input.plays[id];
        }
        if (input.skips && input.skips[id]) {
            t.skippedCount = (t.skippedCount() || 0) + input.skips[id];
        }
    }
}
"""

JXA_METADATA_LISTS = """
var music = Application('Music');
var ft    = music.libraryPlaylists[0].fileTracks;
var artists = new Set();
var albums  = new Set();
var genres  = new Set();

for (var i = 0; i < ft.length; i++) {
    artists.add(ft[i].artist() || 'Unknown Artist');
    albums.add(ft[i].album() || 'Unknown Album');
    genres.add(ft[i].genre() || 'Unknown Genre');
}
JSON.stringify({
    artists: Array.from(artists).sort(),
    albums:  Array.from(albums).sort(),
    genres:  Array.from(genres).sort()
});
"""

class MediaLibrary:
    def get_tracks(self):
        raise NotImplementedError
        
    def get_playlists(self):
        raise NotImplementedError
        
    def get_metadata_lists(self):
        raise NotImplementedError
        
    def update_playlist(self, payload):
        raise NotImplementedError
        
    def import_paths(self, paths):
        raise NotImplementedError
        
    def update_counts(self, plays, skips):
        raise NotImplementedError

class MacMusicLibrary(MediaLibrary):
    def _run_jxa(self, script, arg=None):
        cmd = ['osascript', '-l', 'JavaScript', '-e', script]
        if arg is not None:
            cmd.append(arg)
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or 'osascript failed')
        stdout = r.stdout.strip()
        return json.loads(stdout) if stdout else None

    def get_tracks(self):
        return self._run_jxa(JXA_TRACKS)

    def get_playlists(self):
        return self._run_jxa(JXA_PLAYLISTS)

    def get_metadata_lists(self):
        return self._run_jxa(JXA_METADATA_LISTS)

    def update_playlist(self, payload):
        self._run_jxa(JXA_UPDATE_PLAYLIST, json.dumps(payload))

    def import_paths(self, paths):
        self._run_jxa(JXA_IMPORT, json.dumps(paths))

    def update_counts(self, plays, skips):
        self._run_jxa(JXA_UPDATE_COUNTS, json.dumps({"plays": plays, "skips": skips}))

class WindowsITunesLibrary(MediaLibrary):
    def __init__(self):
        try:
            import win32com.client  # type: ignore
            self.itunes = win32com.client.Dispatch("iTunes.Application")
        except ImportError:
            raise RuntimeError("pywin32 is required to use iTunes on Windows.")
            
    def _get_tid(self, track):
        try:
            high = self.itunes.ITObjectPersistentIDHigh(track)
            low = self.itunes.ITObjectPersistentIDLow(track)
            return f"{(high & 0xFFFFFFFF):08X}{(low & 0xFFFFFFFF):08X}"
        except Exception:
            return ""
            
    def get_tracks(self):
        tracks = []
        library_playlist = None
        for pl in self.itunes.LibrarySource.Playlists:
            if pl.Kind == 1: # ITPlaylistKindLibrary
                library_playlist = pl
                break
        
        if not library_playlist:
            return []
            
        for t in library_playlist.Tracks:
            if t.Kind != 1: # ITTrackKindFile
                continue
            
            tid = self._get_tid(t)
            if not tid:
                continue
            
            try:
                loc = t.Location
                if not loc:
                    continue
            except:
                continue
                
            try:
                mdate = 0
                if t.ModificationDate:
                    try:
                        mdate = int(t.ModificationDate.timestamp() * 1000)
                    except AttributeError:
                        import datetime
                        epoch = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
                        dt = datetime.datetime(
                            t.ModificationDate.year, t.ModificationDate.month, t.ModificationDate.day,
                            t.ModificationDate.hour, t.ModificationDate.minute, t.ModificationDate.second,
                            tzinfo=datetime.timezone.utc
                        )
                        mdate = int((dt - epoch).total_seconds() * 1000)
            except:
                pass
                
            try: loved = t.Loved
            except: loved = False
            
            try: bitRate = t.BitRate
            except: bitRate = 0
            
            try: bpm = t.BPM
            except: bpm = 0
            
            try: trackNumber = t.TrackNumber
            except: trackNumber = 0
            
            try: discNumber = t.DiscNumber
            except: discNumber = 1
            
            try: discCount = t.DiscCount
            except: discCount = 1
            
            try: year = t.Year
            except: year = 0
            
            tracks.append({
                "id": tid,
                "name": t.Name or "",
                "artist": t.Artist or "",
                "albumArtist": t.AlbumArtist or "",
                "album": t.Album or "",
                "genre": t.Genre or "",
                "kind": t.KindAsString or "",
                "trackNumber": trackNumber,
                "discNumber": discNumber,
                "discCount": discCount,
                "mdate": mdate,
                "bitRate": bitRate,
                "bpm": bpm,
                "year": year,
                "composer": t.Composer or "",
                "location": loc,
                "loved": loved
            })
        return tracks

    def get_playlists(self):
        playlists = []
        for pl in self.itunes.LibrarySource.Playlists:
            if pl.Kind != 2: # User playlists
                continue
            
            try:
                name = pl.Name
                is_smart = pl.Smart
            except:
                continue
                
            tids = []
            for t in pl.Tracks:
                if t.Kind == 1:
                    tid = self._get_tid(t)
                    if tid:
                        tids.append(tid)
            
            if tids:
                playlists.append({"name": name, "tracks": tids, "smart": is_smart})
        return playlists

    def get_metadata_lists(self):
        artists = set()
        albums = set()
        genres = set()
        
        library_playlist = None
        for pl in self.itunes.LibrarySource.Playlists:
            if pl.Kind == 1:
                library_playlist = pl
                break
                
        if library_playlist:
            for t in library_playlist.Tracks:
                if t.Kind == 1:
                    try:
                        artists.add(t.Artist or "Unknown Artist")
                        albums.add(t.Album or "Unknown Album")
                        genres.add(t.Genre or "Unknown Genre")
                    except:
                        pass
                        
        return {
            "artists": sorted(list(artists)),
            "albums": sorted(list(albums)),
            "genres": sorted(list(genres))
        }

    def _get_track_by_id(self, tid):
        try:
            high = int(tid[:8], 16)
            if high >= 0x80000000:
                high -= 0x100000000
            low = int(tid[8:], 16)
            if low >= 0x80000000:
                low -= 0x100000000
            return self.itunes.LibraryPlaylist.Tracks.ItemByPersistentID(high, low)
        except:
            return None

    def update_playlist(self, payload):
        if payload.get("is_favorites"):
            for tid in payload.get("add", []):
                t = self._get_track_by_id(tid)
                if t:
                    try: t.Loved = True
                    except: pass
            for tid in payload.get("remove", []):
                t = self._get_track_by_id(tid)
                if t:
                    try: t.Loved = False
                    except: pass
        else:
            name = payload.get("name")
            target_pl = None
            for pl in self.itunes.LibrarySource.Playlists:
                if getattr(pl, 'Name', '') == name and getattr(pl, 'Kind', 0) == 2:
                    target_pl = pl
                    break
            
            if not target_pl:
                return
            
            remove = set(payload.get("remove", []))
            if remove:
                for i in range(target_pl.Tracks.Count, 0, -1):
                    try:
                        t = target_pl.Tracks.Item(i)
                        tid = self._get_tid(t)
                        if tid in remove:
                            t.Delete()
                    except:
                        pass
                        
            for tid in payload.get("add", []):
                t = self._get_track_by_id(tid)
                if t:
                    try: target_pl.AddTrack(t)
                    except: pass

    def import_paths(self, paths):
        for p in paths:
            try:
                self.itunes.LibraryPlaylist.AddFile(p)
            except:
                pass

    def update_counts(self, plays, skips):
        for tid, count in plays.items():
            t = self._get_track_by_id(tid)
            if t:
                try: t.PlayedCount += count
                except: pass
        for tid, count in skips.items():
            t = self._get_track_by_id(tid)
            if t:
                try: t.SkippedCount += count
                except: pass

def get_media_library():
    if sys.platform == 'win32':
        return WindowsITunesLibrary()
    else:
        return MacMusicLibrary()

def sanitize(name, max_len=80):
    name = unicodedata.normalize('NFC', str(name))
    name = re.sub('[<>:"/\\\\|?*\\x00-\\x1f]', '_', name)
    name = name.strip('. ')
    return name[:max_len] or 'Unknown'

def metadata_fp(track, conv_kbps=0):
    fields = ['name', 'artist', 'albumArtist', 'album', 'trackNumber', 'discNumber', 'discCount']
    key = '|'.join(str(track.get(f, '')) for f in fields)
    if conv_kbps > 0 and track.get('bitRate', 0) > conv_kbps:
        key += f"|conv_{conv_kbps}"
    return hashlib.md5(key.encode()).hexdigest()[:16]

def source_fp(src_path):
    """Cheap fingerprint of a source file: size + mtime.
    Used for conversion caching — if the source hasn't changed we can
    reuse the already-converted file on the destination.
    """
    try:
        st = Path(src_path).stat()
        return f"{st.st_size}:{st.st_mtime}"
    except OSError:
        return ""

def dest_path_rel(track, conv_kbps=0):
    loc = track.get('location', '')
    if not loc:
        return None
    ext = Path(loc).suffix.lower()
    if ext not in MEDIA_EXTS:
        return None

    if conv_kbps > 0 and track.get('bitRate', 0) > conv_kbps:
        ext = '.m4a'

    artist = sanitize(track.get('albumArtist') or track.get('artist') or 'Unknown Artist')
    album = sanitize(track.get('album') or 'Unknown Album')
    title = sanitize(track.get('name') or 'Unknown Title')
    track_num = track.get('trackNumber', 0)
    disc_no = track.get('discNumber', 1)
    disc_cnt = track.get('discCount', 1)

    if track_num:
        num = f"{track_num:02d}"
        if disc_cnt and disc_cnt > 1:
            num = f"{disc_no}-{num}"
        filename = f"{num} - {title}{ext}"
    else:
        filename = f"{title}{ext}"

    return Path(artist) / album / filename

def extract_lyrics(src):
    if not HAS_MUTAGEN:
        return ""
    try:
        ext = src.suffix.lower()
        if ext == '.mp3':
            tags = ID3(str(src))
            for key in tags:
                if key.startswith('USLT'):
                    return str(tags[key].text)
        elif ext in ('.m4a', '.aac', '.mp4', '.m4v'):
            tags = MP4(str(src))
            if '\xa9lyr' in tags:
                return str(tags['\xa9lyr'][0])
        elif ext == '.flac':
            audio = FLAC(str(src))
            for k, v in audio.tags.items():
                if k.lower() == 'lyrics':
                    return str(v[0])
    except Exception:
        pass
    return ""

def extract_art_bytes(src):
    if not HAS_MUTAGEN:
        return None
    try:
        ext = src.suffix.lower()
        if ext == '.mp3':
            tags = ID3(str(src))
            for key in tags:
                if key.startswith('APIC'):
                    return tags[key].data
        elif ext in ('.m4a', '.aac'):
            tags = MP4(str(src))
            if 'covr' in tags and tags['covr']:
                return bytes(tags['covr'][0])
        elif ext == '.flac':
            audio = FLAC(str(src))
            if audio.pictures:
                return audio.pictures[0].data
    except Exception:
        pass
    return None

def write_metadata_tags(dst, track):
    """Write Music.app metadata fields into an already-copied file on the destination.

    Called when mdate changed but the file content/path fingerprint did not,
    meaning only metadata was edited on the Apple Music side.  Supports mp3, m4a/aac,
    and flac via mutagen.  Silently returns False if mutagen is unavailable or
    the format is unsupported.
    """
    if not HAS_MUTAGEN:
        return False
    try:
        from mutagen.id3 import (ID3, TIT2, TPE1, TPE2, TALB, TRCK, TPOS,
                                  ID3NoHeaderError)
        from mutagen.mp4 import MP4
        from mutagen.flac import FLAC

        ext = dst.suffix.lower()
        name        = track.get('name', '')
        artist      = track.get('artist', '')
        album_artist= track.get('albumArtist', '') or artist
        album       = track.get('album', '')
        genre       = track.get('genre', '')
        composer    = track.get('composer', '')
        track_num   = track.get('trackNumber', 0)
        disc_num    = track.get('discNumber', 1)
        disc_cnt    = track.get('discCount', 1)
        bpm         = track.get('bpm', 0)
        year        = track.get('year', 0)

        if ext == '.mp3':
            try:
                tags = ID3(str(dst))
            except ID3NoHeaderError:
                tags = ID3()
            tags['TIT2'] = TIT2(encoding=3, text=name)
            tags['TPE1'] = TPE1(encoding=3, text=artist)
            tags['TPE2'] = TPE2(encoding=3, text=album_artist)
            tags['TALB'] = TALB(encoding=3, text=album)
            if track_num:
                tags['TRCK'] = TRCK(encoding=3, text=str(track_num))
            if disc_cnt and disc_cnt > 1:
                tags['TPOS'] = TPOS(encoding=3, text=f'{disc_num}/{disc_cnt}')
            if genre:
                from mutagen.id3 import TCON, TCOM, TBPM, TDRC
                tags['TCON'] = TCON(encoding=3, text=genre)
            if composer:
                from mutagen.id3 import TCON, TCOM, TBPM, TDRC
                tags['TCOM'] = TCOM(encoding=3, text=composer)
            if bpm:
                from mutagen.id3 import TCON, TCOM, TBPM, TDRC
                tags['TBPM'] = TBPM(encoding=3, text=str(int(bpm)))
            if year:
                from mutagen.id3 import TCON, TCOM, TBPM, TDRC
                tags['TDRC'] = TDRC(encoding=3, text=str(year))
            tags.save(str(dst))

        elif ext in ('.m4a', '.aac', '.mp4'):
            tags = MP4(str(dst))
            tags['©nam'] = [name]
            tags['©ART'] = [artist]
            tags['aART']    = [album_artist]
            tags['©alb'] = [album]
            if track_num:
                tags['trkn']  = [(track_num, 0)]
            if disc_cnt and disc_cnt > 1:
                tags['disk']  = [(disc_num, disc_cnt)]
            if genre:
                tags['©gen'] = [genre]
            if composer:
                tags['©wrt'] = [composer]
            if bpm:
                tags['tmpo'] = [int(bpm)]
            if year:
                tags['©day'] = [str(year)]
            tags.save()

        elif ext == '.flac':
            audio = FLAC(str(dst))
            audio['title']        = [name]
            audio['artist']       = [artist]
            audio['albumartist']  = [album_artist]
            audio['album']        = [album]
            if track_num:
                audio['tracknumber'] = [str(track_num)]
            if disc_cnt and disc_cnt > 1:
                audio['discnumber']  = [f'{disc_num}/{disc_cnt}']
            if genre:
                audio['genre']    = [genre]
            if composer:
                audio['composer'] = [composer]
            if bpm:
                audio['bpm']      = [str(int(bpm))]
            if year:
                audio['date']     = [str(year)]
            audio.save()

        else:
            return False

        return True
    except Exception:
        return False

def save_cover(art_bytes, out_path):
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(art_bytes)
        tmp_path = tmp.name
    with tempfile.NamedTemporaryFile(suffix='.bmp', delete=False) as tmp_bmp:
        bmp_path = tmp_bmp.name

    try:
        if sys.platform == 'darwin':
            subprocess.run(['sips', '-s', 'format', 'bmp', '-z', str(ART_SIZE), str(ART_SIZE), tmp_path, '--out', bmp_path], capture_output=True)
            r = subprocess.run(['sips', '-s', 'format', 'jpeg', bmp_path, '--out', str(out_path)], capture_output=True)
            return r.returncode == 0 and out_path.exists()
        else:
            try:
                from PIL import Image
                with Image.open(tmp_path) as img:
                    img = img.convert('RGB')
                    img = img.resize((ART_SIZE, ART_SIZE))
                    img.save(out_path, 'JPEG')
                return out_path.exists()
            except ImportError:
                try:
                    r = subprocess.run(['ffmpeg', '-i', tmp_path, '-vf', f'scale={ART_SIZE}:{ART_SIZE}', '-vframes', '1', '-y', str(out_path)], capture_output=True)
                    return r.returncode == 0 and out_path.exists()
                except Exception:
                    return False
    finally:
        for p in (tmp_path, bmp_path):
            try:
                os.unlink(p)
            except OSError:
                pass


class SyncApp:

    def __init__(self, root):
        self.is_syncing = False
        self.root = root
        self.root.title('Nugget Sync')
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.config = self._load_local_config()
        last_path = self.config.get('last_path', '')
        known_devices = self.config.get('known_devices', [])
        self.valid_path = ""
        self.library = get_media_library()
        
        if last_path and Path(last_path).exists():
            self.valid_path = last_path
        else:
            for d in known_devices:
                if Path(d).exists():
                    self.valid_path = d
                    break

        defaults = {
            'sync_mode': 'all',
            'videos': False,
            'playlists': True,
            'cleanup': True,
            'artwork': bool(HAS_MUTAGEN),
            'auto_sync': False,
            'auto_launch': False,
            'sel_artists': [],
            'sel_albums': [],
            'sel_genres': [],
            'sel_playlists': [],
            'clean_mac': True,
            'backup_rockbox': True,
            'extract_lrc': True,
            'sync_favorites': True,
            'bidirectional_sync': False,
            'sync_rb_playcounts': True,
            'listenbrainz_token': '',
            'convert_bitrate': '0',
            'dry_run': False
        }
        saved_prefs = self.config.get('last_prefs', {})
        self.sync_prefs = {**defaults, **saved_prefs}
        self.is_syncing = False
        self._log_buffer = []
        self._log_text = None
        self._log_win = None
        self._build_ui()
        self._setup_autosync()
        if not self.config.get('first_boot_shown', False):
            self.root.after(100, self._show_welcome_screen)

    def _show_welcome_screen(self):
        win = Toplevel(self.root)
        win.title("Greetings and salutations")
        win.transient(self.root)
        win.grab_set()
        
        self.root.update_idletasks()
        win.update_idletasks()
        
        w = max(520, win.winfo_reqwidth())
        h = max(670, win.winfo_reqheight())
        
        rx = self.root.winfo_x()
        ry = self.root.winfo_y()
        rw = self.root.winfo_width()
        rh = self.root.winfo_height()
        x = rx + (rw - w) // 2
        y = ry + (rh - h) // 2
        
        win.geometry(f"+{x}+{y}")
        win.minsize(520, 670)

        f = ttk.Frame(win, padding=24)
        f.pack(fill='both', expand=True)
        self.welcome_logo_img = tk.PhotoImage(file=resource_path("assets/64.png"))
        logo_label = ttk.Label(f, image=self.welcome_logo_img)
        logo_label.pack(anchor='w', pady=(0, 10))

        ttk.Label(f, text="Get started with Nugget Sync", font=(UI_FONT, 20, 'bold')).pack(anchor='w', pady=(0, 2))
        ttk.Label(f, text="Music library syncing tool for nuggets.", font=(UI_FONT, 12, 'italic'), foreground='#666666').pack(anchor='w', pady=(0, 15))

        def add_section(num_title, body_text):
            ttk.Label(f, text=num_title, font=(UI_FONT, 13, 'bold')).pack(anchor='w', pady=(8, 2))
            lbl = ttk.Label(f, text=body_text, font=(UI_FONT, 11), wraplength=470, justify='left')
            lbl.pack(anchor='w', pady=(0, 8))

        add_section("0. About me", 
                    "Nugget Sync uses your local music library and allows it to be synced to any device or folder. It supports bi-directional changes, favorites syncing, AAC conversion, selection of library to be synced, playlists syncing and Rockbox specific features.")

        add_section("1. Select a drive", 
                    "Go to File > Change Destination…\nNugget Sync will remember this destination on the next app launch.")
        add_section("2. Change settings", 
                    "Go to Nugget Sync > Settings…\nNugget Sync will remember settings for this destination.")
        add_section("3. Sync to destination", 
                    "Click ‘Do it.’ on the main UI screen. It will sync shortly.\nPlease ensure to allow any permissions, such as your Music library and access to removable drives.")
        add_section("4. Upon completion", 
                    "Once the sync is completed, you can safely eject. Nugget Sync can do it from the UI if you wish.\n\n"
                    "Important note: A .NUGLIB file will be created in the destination device. DO NOT DELETE IT. "
                    "It manages the sync settings for the device and the internal library. If deleted, sync will run slower and potential data loss will occur.")

        def close_welcome():
            self.config['first_boot_shown'] = True
            self._save_local_config()
            win.grab_release()
            win.destroy()

        btn = ttk.Button(f, text="Okay, I'm done", command=close_welcome)
        btn.pack(side='bottom', pady=(15, 0))
        
        win.protocol("WM_DELETE_WINDOW", close_welcome)

    def _on_closing(self):
        if hasattr(self, 'is_syncing') and self.is_syncing:
            messagebox.showwarning(
                "Sync in Progress",
                "Wait for the sync to complete before closing"
            )
        else:
            self.root.destroy()

    def _load_local_config(self):
        if LOCAL_CONFIG.exists():
            try:
                cfg = json.loads(LOCAL_CONFIG.read_text())
                if isinstance(cfg, dict) and 'first_boot_shown' not in cfg:
                    cfg['first_boot_shown'] = True
                return cfg
            except:
                pass
        return {'last_path': '', 'known_devices': [], 'first_boot_shown': False}

    def _save_local_config(self):
        self.config['last_path'] = self.dest_var.get()
        self.config['last_prefs'] = self.sync_prefs
        
        known = self.config.get('known_devices', [])
        if self.config['last_path'] and self.config['last_path'] not in known:
            known.insert(0, self.config['last_path'])
        self.config['known_devices'] = known
        
        LOCAL_CONFIG.write_text(json.dumps(self.config))

    def _process_rockbox_log(self, dest_root, tracks_list):
        log_file = dest_root / '.scrobbler.log'
        if not log_file.exists(): return
        
        token = self.sync_prefs.get('listenbrainz_token', '').strip()
        if token.lower().startswith('token '): token = token[6:].strip()
        do_lb = bool(token)
        do_plays = self.sync_prefs.get('sync_rb_playcounts', True)
        
        if not (do_lb or do_plays): return
        
        self._log("Processing scrobbles", 'info')
        listens = []
        play_updates = {}
        skip_updates = {}
        track_map = {}
        if do_plays:
            for t in tracks_list:
                key = f"{t.get('artist','')}|{t.get('album','')}|{t.get('name','')}".lower()
                track_map[key] = t.get('id')
                
        try:
            lines = log_file.read_text(encoding='utf-8', errors='replace').splitlines()
            for line in lines:
                if line.startswith('#') or not line.strip(): continue
                parts = line.split('\t')
                
                if len(parts) >= 7:
                    artist = parts[0].strip() or "Unknown Artist"
                    album = parts[1].strip()
                    title = parts[2].strip() or "Unknown Track"
                    rating = parts[5]
                    timestamp = int(parts[6])
                    
                    if do_plays:
                        key = f"{artist}|{album}|{title}".lower()
                        tid = track_map.get(key)
                        if tid:
                            if rating == 'L': play_updates[tid] = play_updates.get(tid, 0) + 1
                            elif rating == 'S': skip_updates[tid] = skip_updates.get(tid, 0) + 1
                            
                    if do_lb and rating == 'L':
                        track_metadata = {"artist_name": artist, "track_name": title}
                        if album: track_metadata["release_name"] = album
                        listens.append({"listened_at": timestamp, "track_metadata": track_metadata})
        except Exception as e:
            self._log(f"Error reading scrobbler log: {e}")
            return
        if do_plays and (play_updates or skip_updates):
            try:
                self.library.update_counts(play_updates, skip_updates)
                self._log(f"Synced {sum(play_updates.values())} plays and {sum(skip_updates.values())} skips to Local Library.", 'ok')
            except Exception as e:
                self._log(f"Error syncing playcounts: {e}", 'del')
        if do_plays and not do_lb:
            try: log_file.unlink()
            except: pass
        if do_lb and listens:
            success = True
            for i in range(0, len(listens), 1000):
                batch = listens[i:i+1000]
                payload = json.dumps({"listen_type": "import", "payload": batch}).encode('utf-8')
                req = urllib.request.Request("https://api.listenbrainz.org/1/submit-listens", data=payload, headers={
                    "Authorization": f"Token {token}",
                    "Content-Type": "application/json"
                })
                try:
                    try:
                        with urllib.request.urlopen(req): pass
                    except Exception as e_inner:
                        if 'CERTIFICATE_VERIFY_FAILED' in str(e_inner):
                            import ssl
                            ctx = ssl.create_default_context()
                            ctx.check_hostname = False
                            ctx.verify_mode = ssl.CERT_NONE
                            with urllib.request.urlopen(req, context=ctx): pass
                        else: raise e_inner
                except Exception as e:
                    self._log(f"ListenBrainz API Error: {e}", 'del')
                    success = False
                    break
            if success:
                self._log(f"Scrobbled {len(listens)} tracks to ListenBrainz.", 'ok')
                try: log_file.unlink()
                except: pass

    def _backup_rockbox(self, dest_root):
        rb_dir = dest_root / '.rockbox'
        if rb_dir.exists():
            self._log("Backing up .rockbox directory", 'info')
            backup_dir = Path.home() / 'Rockbox_Backup'
            try:
                if backup_dir.exists():
                    shutil.rmtree(backup_dir, ignore_errors=True)
                shutil.copytree(rb_dir, backup_dir)
            except Exception as e:
                self._log(f"Failed to backup .rockbox: {e}", 'del')

    def _ask_import_conflict(self, count):
        result = [None]
        ev = threading.Event()
        def prompt():
            win = Toplevel(self.root)
            win.title("Unknown Files Found")
            win.minsize(380, 110)
            win.attributes('-topmost', True)
            win.grab_set()
            ttk.Label(win, text=f"Found {count} unrecognized files on the destination.\nWould you like to import them to your Local Library?", justify="center").pack(pady=10)
            btn_frame = ttk.Frame(win)
            btn_frame.pack(fill='x', padx=10, pady=5)
            def choose(c):
                result[0] = c
                win.grab_release()
                win.destroy()
                ev.set()
            ttk.Button(btn_frame, text="Import to Local Library", command=lambda: choose('import')).pack(side='left', expand=True, padx=2)
            ttk.Button(btn_frame, text="Delete", command=lambda: choose('delete')).pack(side='left', expand=True, padx=2)
            ttk.Button(btn_frame, text="Ignore", command=lambda: choose('ignore')).pack(side='left', expand=True, padx=2)
            win.protocol("WM_DELETE_WINDOW", lambda: choose('ask'))
        self.root.after(0, prompt)
        ev.wait()
        return result[0]

    def _clean_mac_files(self, dest_root):
        self._log("Cleaning up temporary files", 'dim')
        for root_str, dirs, files in os.walk(dest_root):
            root_path = Path(root_str)
            for f in files:
                if f == '.DS_Store' or f.startswith('._'):
                    try:
                        (root_path / f).unlink()
                    except:
                        pass
        for d in ['.Spotlight-V100', '.Trashes', '.fseventsd']:
            p = dest_root / d
            if p.exists():
                try:
                    shutil.rmtree(p, ignore_errors=True)
                except:
                    pass

    def _show_about(self):
        messagebox.showinfo("About Nugget Sync", "Nugget Sync\nMusic library syncing tool for nuggets.\n© 2026")

    def _build_ui(self):
        self.root.configure(bg='#ececec')

        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Sync Now", command=self._start_sync, accelerator="Cmd+R")
        file_menu.add_separator()
        file_menu.add_command(label="Change Destination…", command=self._pick_dest, accelerator="Cmd+D")
        menubar.add_cascade(label="File", menu=file_menu)
        
        edit_menu = tk.Menu(menubar, tearoff=False)
        edit_menu.add_command(label="Preferences", command=self._open_preferences, accelerator="Cmd+,")
        menubar.add_cascade(label="Edit", menu=edit_menu)

        view_menu = tk.Menu(menubar, tearoff=False)
        view_menu.add_command(label="Sync Log", command=self._open_log_window, accelerator="Cmd+L")
        menubar.add_cascade(label="View", menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="Greetings and salutations", command=self._show_welcome_screen)
        help_menu.add_command(label="About Nugget Sync", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.createcommand("tk::mac::ShowPreferences", self._open_preferences)
        
        self.root.bind('<Command-r>', lambda e: self._start_sync() if self.sync_btn['state'] != 'disabled' else None)
        self.root.bind('<Command-d>', lambda e: self._pick_dest())
        self.root.bind('<Command-l>', lambda e: self._open_log_window())
        self.root.bind('<Command-comma>', lambda e: self._open_preferences())

        outer = ttk.Frame(self.root, padding=(26, 20))
        outer.grid(sticky='nsew')

        header_frame = ttk.Frame(outer)
        header_frame.grid(row=0, sticky='ew', pady=(0, 20))

        self.logo_img = tk.PhotoImage(file=resource_path("assets/music.png"))

        self.dest_var = StringVar(value=self.valid_path)

        self.header_label = ttk.Label(
            header_frame,
            text='  Nugget Sync',
            font=(UI_FONT, 24, 'bold'),
            image=self.logo_img,
            compound='left',
        )
        self.header_label.pack(side='left')
        
        self.sync_btn = ttk.Button(
            header_frame,
            text='Do it.',
            command=self._start_sync,
            default='active'
        )
        self.sync_btn.pack(side='right')

        def update_header(*args):
            dest = self.dest_var.get()
            if dest and Path(dest).exists():
                folder_name = Path(dest).name
                self.header_label.config(text=f'  Sync to {folder_name}')
                self.sync_btn.config(state='normal')
                manifest_path = Path(dest) / MANIFEST_FILE
                if manifest_path.exists():
                    try:
                        data = json.loads(manifest_path.read_text())
                        prefs = data.get('sync_preferences')
                        if prefs:
                            self.sync_prefs.update(prefs)
                    except Exception:
                        pass
            else:
                self.header_label.config(text='  Cannot sync.')
                self.sync_btn.config(state='disabled')

        self.dest_var.trace_add('write', update_header)
        update_header()
        
        self.cancel_btn = ttk.Button(
            header_frame,
            text='STOP',
            command=self._cancel_sync
        )

        self.progress = ttk.Progressbar(outer, length=500, mode='determinate')
        self.progress.grid(row=2, sticky='ew')
        
        self.root.update_idletasks()
        self.root.minsize(self.root.winfo_reqwidth(), self.root.winfo_reqheight())

    def _open_preferences(self):
        win = Toplevel(self.root)
        win.title("Preferences")
        win.minsize(450, 700)
        f = ttk.Frame(win, padding=15)
        f.pack(fill='both', expand=True)

        ttk.Label(f, text="Sync Content",
                  font=(UI_FONT, 13, 'bold')).pack(anchor='w', pady=(0, 5))
        mode_var = StringVar(value=self.sync_prefs.get('sync_mode', 'all'))
        ttk.Radiobutton(f,
                        text="Entire Library",
                        variable=mode_var,
                        value='all').pack(anchor='w')
        ttk.Radiobutton(f,
                        text="Selected Items...",
                        variable=mode_var,
                        value='filtered').pack(anchor='w')

        def configure_items():
            self._open_item_selector()

        conf_btn = ttk.Button(f,
                              text="Select Artists/Albums/Genres...",
                              command=configure_items)
        conf_btn.pack(pady=5)
        mode_var.trace_add(
            "write", lambda *args: conf_btn.config(state='normal'
                                                   if mode_var.get() ==
                                                   'filtered' else 'disabled'))
        conf_btn.config(
            state='normal' if mode_var.get() == 'filtered' else 'disabled')

        ttk.Label(f,
                  text="Convert higher bitrate songs to...").pack(anchor='w',
                                                                  pady=(0, 2))
        conv_var = StringVar(
            value=str(self.sync_prefs.get('convert_bitrate', '0')))
        f_conv = ttk.Frame(f)
        f_conv.pack(anchor='w', pady=(0, 10))
        ttk.Radiobutton(f_conv, text="Off", variable=conv_var,
                        value='0').pack(side='left', padx=(0, 5))
        ttk.Radiobutton(f_conv,
                        text="AAC 256 kbps",
                        variable=conv_var,
                        value='256').pack(side='left', padx=5)
        ttk.Radiobutton(f_conv,
                        text="AAC 192 kbps",
                        variable=conv_var,
                        value='192').pack(side='left', padx=5)
        ttk.Radiobutton(f_conv,
                        text="AAC 128 kbps",
                        variable=conv_var,
                        value='128').pack(side='left', padx=5)

        ttk.Separator(f).pack(fill='x', pady=10)

        vid_var = BooleanVar()
        vid_var.set(bool(self.sync_prefs.get('videos', False)))
        ttk.Checkbutton(f, text="Sync Videos",
                        variable=vid_var).pack(anchor='w')

        pl_var = BooleanVar()
        pl_var.set(bool(self.sync_prefs.get('playlists', True)))
        ttk.Checkbutton(f, text="Sync Playlists",
                        variable=pl_var).pack(anchor='w')

        rem_var = BooleanVar()
        rem_var.set(bool(self.sync_prefs.get('cleanup', True)))
        ttk.Checkbutton(f,
                        text="Sync Modifications of Library",
                        variable=rem_var).pack(anchor='w')

        fav_var = BooleanVar(value=self.sync_prefs.get('sync_favorites', True))
        ttk.Checkbutton(f,
                        text="Sync Favorites to a Playlist",
                        variable=fav_var).pack(anchor='w')

        bidi_var = BooleanVar(
            value=self.sync_prefs.get('bidirectional_sync', False))
        ttk.Checkbutton(f,
                        text="Enable Bi-directional Playlists/Favorites Sync",
                        variable=bidi_var).pack(anchor='w')

        ttk.Separator(f).pack(fill='x', pady=10)

        art_var = BooleanVar()
        art_var.set(bool(self.sync_prefs.get('artwork', HAS_MUTAGEN)))
        ttk.Checkbutton(
            f,
            text="Export album art to cover.jpg",
            variable=art_var,
            state='normal' if HAS_MUTAGEN else 'disabled').pack(anchor='w')

        lrc_var = BooleanVar(value=self.sync_prefs.get('extract_lrc', True))
        ttk.Checkbutton(f,
                        text="Extract lyrics to .lrc files",
                        variable=lrc_var).pack(anchor='w')

        clean_mac_var = BooleanVar(
            value=self.sync_prefs.get('clean_mac', True))
        ttk.Checkbutton(f,
                        text="Clean temporary files",
                        variable=clean_mac_var).pack(anchor='w')

        ttk.Separator(f).pack(fill='x', pady=10)

        auto_var = BooleanVar()
        auto_var.set(bool(self.sync_prefs.get('auto_sync', False)))
        ttk.Checkbutton(f,
                        text="Auto-sync when drive connected",
                        variable=auto_var).pack(anchor='w')

        launch_var = BooleanVar()
        launch_var.set(bool(self.sync_prefs.get('auto_launch', False)))
        if sys.platform == 'darwin':
            ttk.Checkbutton(f,
                            text="Auto-launch app when drive connected",
                            variable=launch_var).pack(anchor='w')

        ttk.Separator(f).pack(fill='x', pady=10)
        ttk.Label(f, text="Rockbox features",
                  font=(UI_FONT, 13, 'bold')).pack(anchor='w', pady=(0, 5))

        playcounts_var = BooleanVar(value=self.sync_prefs.get('sync_rb_playcounts', True))
        ttk.Checkbutton(f, text="Sync database information", variable=playcounts_var).pack(anchor='w')

        backup_rb_var = BooleanVar(
            value=self.sync_prefs.get('backup_rockbox', True))
        ttk.Checkbutton(f,
                        text="Backup .rockbox folder locally",
                        variable=backup_rb_var).pack(anchor='w')

        ttk.Label(f, text="ListenBrainz token for scrobble:").pack(anchor='w',
                                                                   pady=(5, 0))
        lb_token_var = StringVar(
            value=self.sync_prefs.get('listenbrainz_token', ''))
        ttk.Entry(f, textvariable=lb_token_var, width=45).pack(anchor='w')

        ttk.Separator(f).pack(fill='x', pady=10)

        dry_run_var = BooleanVar(value=self.sync_prefs.get('dry_run', False))
        ttk.Checkbutton(f,
                        text="Preview changes (Dry run)",
                        variable=dry_run_var).pack(anchor='w')

        ttk.Separator(f).pack(fill='x', pady=10)

        def save():
            self.sync_prefs.update({
                'convert_bitrate': conv_var.get(),
                'clean_mac': clean_mac_var.get(),
                'backup_rockbox': backup_rb_var.get(),
                'extract_lrc': lrc_var.get(),
                'sync_favorites': fav_var.get(),
                'bidirectional_sync': bidi_var.get(),
                'sync_rb_playcounts': playcounts_var.get(),
                'listenbrainz_token': lb_token_var.get(),
                'sync_mode': mode_var.get(),
                'videos': vid_var.get(),
                'playlists': pl_var.get(),
                'cleanup': rem_var.get(),
                'artwork': art_var.get(),
                'auto_sync': auto_var.get(),
                'auto_launch': launch_var.get(),
                'dry_run': dry_run_var.get()
            })
            self._manage_launch_agent(launch_var.get())
            self._save_local_config()
            win.destroy()

        ttk.Button(f, text="Save", command=save).pack(pady=10)

    def _get_plist_path(self):
        return Path.home(
        ) / "Library/LaunchAgents/com.norocoral.nuggetsync.autosync.plist"

    def _manage_launch_agent(self, enable):
        if sys.platform != 'darwin':
            return
        plist_path = self._get_plist_path()
        if enable:
            script_path = os.path.abspath(sys.argv[0])
            python_path = sys.executable
            plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.norocoral.nuggetsync.autosync</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>{script_path}</string>
        <string>--auto</string>
    </array>
    <key>WatchPaths</key>
    <array>
        <string>/Volumes</string>
    </array>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>"""
            plist_path.parent.mkdir(parents=True, exist_ok=True)
            plist_path.write_text(plist_content)
            subprocess.run(
                ["launchctl", "load", str(plist_path)], capture_output=True)
        else:
            subprocess.run(
                ["launchctl", "unload", str(plist_path)], capture_output=True)
            if plist_path.exists():
                plist_path.unlink()

    def _ask_sync_conflict(self, pl_name, mac_mtime=None, dap_mtime=None):
        """Ask the user how to resolve a playlist conflict.

        mac_mtime / dap_mtime are Unix timestamps (float) for the last known
        modification time of each side.  When both are provided the dialog
        shows which copy is newer so the user can make an informed choice.
        Closing the window without choosing returns 'ask' so the caller can
        treat it as unresolved rather than silently picking a default.
        """
        import time as _time

        result = [None]
        ev = threading.Event()

        def _fmt(ts):
            try:
                return _time.strftime('%Y-%m-%d %H:%M', _time.localtime(ts))
            except Exception:
                return 'unknown'

        def prompt():
            win = Toplevel(self.root)
            win.title("Playlist Conflict")
            win.attributes('-topmost', True)
            win.grab_set()

            if mac_mtime is not None and dap_mtime is not None:
                newer = "Local Library" if mac_mtime >= dap_mtime else "Nugget"
                msg = (
                    f'Both sides of "{pl_name}" changed since the last sync.\n\n'
                    f'  Local Library last modified:    {_fmt(mac_mtime)}\n'
                    f'  Nugget last modified: {_fmt(dap_mtime)}\n\n'
                    f'Newer copy: {newer}'
                )
            else:
                msg = (
                    f'Both sides of "{pl_name}" changed since the last sync\n'
                    'and modification times could not be determined.\n\n'
                    'Choose which version to keep, or merge both.'
                )

            ttk.Label(win, text=msg, justify="left",
                      padding=(14, 12, 14, 0)).pack()

            btn_frame = ttk.Frame(win, padding=(10, 10))
            btn_frame.pack(fill='x')

            def choose(choice):
                result[0] = choice
                win.grab_release()
                win.destroy()
                ev.set()

            ttk.Button(btn_frame,
                       text="Keep Local Library",
                       command=lambda: choose('mac')).pack(side='left',
                                                           expand=True,
                                                           padx=2)
            ttk.Button(btn_frame,
                       text="Keep Nugget",
                       command=lambda: choose('dap')).pack(side='left',
                                                           expand=True,
                                                           padx=2)
            ttk.Button(btn_frame,
                       text="Merge Both",
                       command=lambda: choose('merge')).pack(side='left',
                                                             expand=True,
                                                             padx=2)

            win.update_idletasks()
            win.minsize(win.winfo_reqwidth(), win.winfo_reqheight())
            win.protocol("WM_DELETE_WINDOW", lambda: (
                win.grab_release(), win.destroy(), ev.set()
            ))

        self.root.after(0, prompt)
        ev.wait()
        return result[0] if result[0] is not None else 'ask'

    def _log(self, msg, tag=''):
        if not msg.strip():
            return
        self._log_buffer.append(msg)
        self.root.after(0, lambda m=msg: self._append_log_window(m))

    def _append_log_window(self, msg):
        """Append a line to the log window Text widget if it is open."""
        tv = getattr(self, '_log_text', None)
        if tv is None:
            return
        try:
            tv.configure(state='normal')
            tv.insert('end', msg + '\n')
            tv.see('end')
            tv.configure(state='disabled')
        except Exception:
            self._log_text = None

    def _open_log_window(self):
        """Open (or raise) the sync log window."""
        if getattr(self, '_log_win', None) and self._log_win.winfo_exists():
            self._log_win.lift()
            return
        win = Toplevel(self.root)
        win.title("Sync Log")
        win.minsize(700, 450)
        self._log_win = win

        f = ttk.Frame(win)
        f.pack(fill='both', expand=True, padx=8, pady=8)

        tv = tk.Text(f, wrap='none', state='disabled',
                     font=(MONO_FONT, 11), bg='#1e1e1e', fg='#d4d4d4',
                     insertbackground='white')
        tv.pack(side='left', fill='both', expand=True)
        sb = ttk.Scrollbar(f, orient='vertical', command=tv.yview)
        sb.pack(side='right', fill='y')
        tv['yscrollcommand'] = sb.set
        self._log_text = tv
        tv.configure(state='normal')
        for line in self._log_buffer:
            tv.insert('end', line + '\n')
        tv.see('end')
        tv.configure(state='disabled')

        btn_row = ttk.Frame(win)
        btn_row.pack(fill='x', padx=8, pady=(0, 8))

        def clear_log():
            self._log_buffer.clear()
            tv.configure(state='normal')
            tv.delete('1.0', 'end')
            tv.configure(state='disabled')

        ttk.Button(btn_row, text="Clear", command=clear_log).pack(side='right')

        def on_close():
            self._log_text = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)

    def _pick_dest(self):
        p = filedialog.askdirectory(title='Select Destination Folder')
        if p:
            self.dest_var.set(p)

    def _open_item_selector(self):
        selector = Toplevel(self.root)
        selector.title("Select Items to Sync")
        selector.minsize(500, 600)

        self._status("Loading metadata")
        data = self.library.get_metadata_lists()
        pls = self.library.get_playlists()
        self._status("")

        nb = ttk.Notebook(selector)
        nb.pack(fill='both', expand=True, padx=10, pady=10)

        lists = {}
        for label, key in [("Artists", "artists"), ("Albums", "albums"),
                           ("Genres", "genres")]:
            frame = ttk.Frame(nb)
            nb.add(frame, text=label)
            lb = Listbox(frame, selectmode='multiple', exportselection=0)
            lb.pack(fill='both', expand=True, side='left')
            sb = ttk.Scrollbar(frame, orient="vertical", command=lb.yview)
            sb.pack(side='right', fill='y')
            lb.config(yscrollcommand=sb.set)

            for item in data[key]:
                lb.insert(END, item)
                if item in self.sync_prefs.get(f'sel_{key}', []):
                    lb.selection_set(END)
            lists[key] = lb
        pl_frame = ttk.Frame(nb)
        nb.add(pl_frame, text="Playlists")
        pl_lb = Listbox(pl_frame, selectmode='multiple', exportselection=0)
        pl_lb.pack(fill='both', expand=True, side='left')
        for p in pls:
            pl_lb.insert(END, p['name'])
            if p['name'] in self.sync_prefs.get('sel_playlists', []):
                pl_lb.selection_set(END)
        lists['playlists'] = pl_lb

        def save_selection():
            for key, lb in lists.items():
                self.sync_prefs[f'sel_{key}'] = [
                    lb.get(i) for i in lb.curselection()
                ]
            selector.destroy()

        ttk.Button(selector, text="Done", command=save_selection).pack(pady=10)

    def _status(self, msg):
        self.last_status_msg = msg
        self._update_eta_displays()

    def _update_eta_displays(self):
        def update():
            v = getattr(self, 'current_pct', 0)
            msg = getattr(self, 'last_status_msg', '')
            
            eta_str = ""
            if getattr(self, 'is_syncing', False) and v > 0 and v < 100:
                smoothed = getattr(self, 'smoothed_eta', None)
                if smoothed is not None:
                    mins, secs = divmod(max(1, int(smoothed)), 60)
                    eta_str = f" - {mins:02d}m {secs:02d}s remaining"
                else:
                    eta_str = " - Calculating ETA"

            title_str = f"Nugget Sync{eta_str}" if getattr(self, 'is_syncing', False) else "Nugget Sync"
            self.root.title(title_str)
            
            if msg:
                self.header_label.config(text=f"  {msg}")

        self.root.after(0, update)

    def _eta_loop(self):
        if getattr(self, 'is_syncing', False):
            v = getattr(self, 'current_pct', 0)
            if v > 0 and v < 100 and hasattr(self, 'sync_start_time'):
                import time
                elapsed = time.time() - self.sync_start_time
                if elapsed > 3 and v >= 2:
                    raw_eta = (elapsed / v) * (100 - v)
                    if getattr(self, 'smoothed_eta', None) is None:
                        self.smoothed_eta = raw_eta
                    else:
                        alpha = 0.10
                        self.smoothed_eta = (alpha * raw_eta) + ((1 - alpha) * self.smoothed_eta)

            self._update_eta_displays()
            self.root.after(1000, self._eta_loop)

    def _setup_autosync(self):
        self.is_syncing = False

        def _check_drive():
            import time
            last_path = self.dest_var.get()
            last_state = Path(last_path).exists()

            while True:
                time.sleep(3)
                current_path = self.dest_var.get()
                current_state = Path(current_path).exists()

                if current_path != last_path:
                    last_path = current_path
                    last_state = current_state
                    continue
                if current_state and not last_state:
                    time.sleep(
                        2)
                    if Path(current_path).exists(
                    ) and not self.is_syncing and self.sync_prefs.get(
                            'auto_sync'):
                        manifest_exists = (Path(current_path) /
                                           MANIFEST_FILE).exists()
                        if manifest_exists:
                            self._log(
                                "Auto-sync triggered by volume attachment")
                            self.root.after(0, self._start_sync)

                last_state = current_state

        threading.Thread(target=_check_drive, daemon=True).start()

    def _pct(self, v):
        self.current_pct = v
        self.root.after(0, lambda: self.progress.configure(value=v))
        self._update_eta_displays()

    def _start_sync(self):
        if hasattr(self, 'is_syncing') and self.is_syncing:
            return
        self.is_syncing = True
        self._cancel_requested = False
        self.sync_start_time = __import__('time').time()
        self.current_pct = 0
        self.smoothed_eta = None
        self.sync_btn.pack_forget()
        self.cancel_btn.pack(side='right')
        self.cancel_btn.config(state='normal')
        self.progress['value'] = 0
        self._status('Starting sync')
        self._eta_loop()
        threading.Thread(target=self._sync, daemon=True).start()

    def _cancel_sync(self):
        if self.is_syncing:
            self._cancel_requested = True
            self.cancel_btn.config(state='disabled')
            self._status('Cancelling...')
            self._log('Please wait', 'dim')

    def _sync(self):
        dest_path = self.dest_var.get()
        if not dest_path or not Path(dest_path).exists():
            self._status('Invalid destination')
            self.root.after(0, self._cancel_sync)
            return
            
        dest_root = Path(dest_path)
        if dest_root in (Path('/'), Path.home(), Path('/Volumes')):
            messagebox.showerror("Invalid Destination", "Cannot sync to root or system directories. Select a valid removable drive or folder.")
            self.root.after(0, self._cancel_sync)
            return

        conv_kbps = int(self.sync_prefs.get('convert_bitrate', '0'))
        dry_run   = bool(self.sync_prefs.get('dry_run', False))

        if dry_run:
            self._log('No files will be written or deleted', 'info')
            self._status('Dry run enabled')

        self._log('Using your local music library', 'info')
        self._status('Checking music library')
        self._pct(2)

        try:
            tracks_list = self.library.get_tracks()
        except Exception as exc:
            self._log(f"Could not read library: {exc}", 'del')
            self.root.after(0, lambda: self.sync_btn.config(state='normal'))
            return

        self._log(f"   {len(tracks_list)} local tracks found", 'dim')
        playlists = []
        if self.sync_prefs.get('playlists', True):
            self._status('Checking playlists')
            try:
                playlists = self.library.get_playlists()
                self._log(f"   {len(playlists)} user playlists\n", 'dim')
            except Exception:
                pass

        dest_root.mkdir(parents=True, exist_ok=True)
        music_dir = dest_root / 'Music'
        playlists_dir = dest_root / 'Playlists'
        legacy_pl_dir = dest_root / '_Playlists'
        if legacy_pl_dir.exists() and legacy_pl_dir.is_dir():
            shutil.rmtree(legacy_pl_dir, ignore_errors=True)

        manifest_path = dest_root / MANIFEST_FILE
        manifest = {}
        is_new_library = False

        last_sync_time = 0.0
        last_playlist_state = {}
        last_playlist_mtime_snapshot = {}
        dismissed_unrecognized = set()
        if manifest_path.exists():
            try:
                data = json.loads(manifest_path.read_text())
                manifest = data.get('library_data', data) if isinstance(
                    data, dict) else {}
                last_playlist_state = data.get(
                    'playlist_state', {}) if isinstance(data, dict) else {}
                last_playlist_mtime_snapshot = data.get(
                    'playlist_mtime_snapshot', {}) if isinstance(data, dict) else {}
                dismissed_unrecognized = set(data.get(
                    'dismissed_unrecognized', [])) if isinstance(data, dict) else set()
                last_sync_time = float(
                    data.get('last_sync_timestamp', 0)) if isinstance(
                        data, dict) else 0.0
            except Exception:
                pass
        if self.sync_prefs.get('bidirectional_sync') and last_sync_time > 0:
            self._status("Checking destination for metadata/playlist changes...")
            path_to_tid = {}
            for tid, entry in manifest.items():
                if isinstance(entry, dict) and entry.get('dest'):
                    norm_path = unicodedata.normalize('NFC', "/" + entry['dest'])
                    path_to_tid[norm_path] = tid

            for track in tracks_list:
                tid = track.get('id')
                rel = dest_path_rel(track, conv_kbps)
                if tid and rel:
                    norm_path = unicodedata.normalize('NFC', "/" + (Path("Music") / rel).as_posix())
                    if norm_path not in path_to_tid:
                        path_to_tid[norm_path] = tid

            if playlists_dir.exists():
                for pl_file in playlists_dir.glob('*.m3u8'):
                    try:
                        dap_mtime = pl_file.stat().st_mtime
                        if dap_mtime <= last_sync_time:
                            continue

                        dap_paths = []
                        for line in pl_file.read_text(
                                encoding='utf-8').splitlines():
                            line = line.strip()
                            if not line.startswith('#') and line:
                                line = unicodedata.normalize('NFC', line)
                                p = line.replace('\\', '/')
                                if p.startswith('../'): p = '/' + p[3:]
                                elif not p.startswith('/'): p = '/' + p
                                dap_paths.append(p)

                        dap_tids = set(path_to_tid[p] for p in dap_paths
                                       if p in path_to_tid)
                        pl_name = pl_file.stem
                        if pl_name == "Favorites" and self.sync_prefs.get('sync_favorites'):
                            mac_tids = set(t['id'] for t in tracks_list if t.get('loved'))

                            if pl_name in last_playlist_state:
                                last_tids = set(last_playlist_state[pl_name])
                                mac_changed = (mac_tids != last_tids)
                                dap_changed = (dap_tids != last_tids)

                                if dap_changed and not mac_changed:
                                    add_tids = list(dap_tids - last_tids)
                                    remove_tids = list(last_tids - dap_tids)
                                elif mac_changed and not dap_changed:
                                    add_tids, remove_tids = [], []
                                elif dap_changed and mac_changed:
                                    mac_mtime = last_playlist_mtime_snapshot.get(pl_name)
                                    if mac_mtime is not None:
                                        if dap_mtime >= mac_mtime:
                                            add_tids = list(dap_tids - last_tids)
                                            remove_tids = list(last_tids - dap_tids)
                                            self._log(f"Applying destination favorite changes", 'dim')
                                        else:
                                            add_tids, remove_tids = [], []
                                            self._log(f"Using local favorites", 'dim')
                                    else:
                                        resolution = self._ask_sync_conflict(
                                            "Favorites", mac_mtime=None, dap_mtime=None)
                                        if resolution == 'dap':
                                            add_tids = list(dap_tids - last_tids)
                                            remove_tids = list(last_tids - dap_tids)
                                        elif resolution == 'merge':
                                            merged = mac_tids.union(dap_tids)
                                            add_tids = list(merged - mac_tids)
                                            remove_tids = []
                                        else:
                                            add_tids, remove_tids = [], []
                                else:
                                    add_tids, remove_tids = [], []
                            else:
                                add_tids = list(dap_tids - mac_tids)
                                remove_tids = []

                            if add_tids or remove_tids:
                                self._log("Syncing favorites to Local Library", 'info')
                                self.library.update_playlist({"is_favorites": True, "add": add_tids, "remove": remove_tids})
                                for t in tracks_list:
                                    if t['id'] in add_tids: t['loved'] = True
                                    elif t['id'] in remove_tids: t['loved'] = False
                            continue
                        target_pl = next((p for p in playlists if sanitize(p['name']) == pl_name), None)
                        if target_pl and not target_pl.get('smart'):
                            mac_tids = set(target_pl['tracks'])
                        else:
                            continue

                        if dap_tids == mac_tids:
                            continue

                        resolution = None
                        if pl_name in last_playlist_state:
                            last_tids = set(last_playlist_state[pl_name])
                            mac_changed = (mac_tids != last_tids)
                            dap_changed = (dap_tids != last_tids)

                            if dap_changed and not mac_changed:
                                resolution = 'dap'
                            elif mac_changed and not dap_changed:
                                resolution = 'mac'
                            elif dap_changed and mac_changed:
                                mac_mtime_snap = last_playlist_mtime_snapshot.get(pl_name)
                                if mac_mtime_snap is not None:
                                    if dap_mtime >= mac_mtime_snap:
                                        resolution = 'dap'
                                        self._log(f"Applying destination {pl_name} changes", 'dim')
                                    else:
                                        resolution = 'mac'
                                        self._log(f"Using local {pl_name}", 'dim')
                                else:
                                    resolution = self._ask_sync_conflict(
                                        pl_name, mac_mtime=None, dap_mtime=dap_mtime)
                        else:
                            mac_mtime_snap = last_playlist_mtime_snapshot.get(pl_name)
                            if mac_mtime_snap is not None:
                                if dap_mtime >= mac_mtime_snap:
                                    resolution = 'dap'
                                else:
                                    resolution = 'mac'
                            else:
                                resolution = self._ask_sync_conflict(
                                    pl_name, mac_mtime=mac_mtime_snap, dap_mtime=dap_mtime)

                        if resolution == 'ask' or resolution is None:
                            self._log(f"Skipping {pl_name} as the conflict is unresolved", 'dim')
                            continue

                        if resolution == 'mac':
                            self._log(f"Using local {pl_name}", 'dim')
                            continue

                        add_tids = []
                        remove_tids = []

                        if resolution == 'dap':
                            add_tids = list(dap_tids - mac_tids)
                            remove_tids = list(mac_tids - dap_tids)
                        elif resolution == 'merge':
                            merged_tids = mac_tids.union(dap_tids)
                            add_tids = list(merged_tids - mac_tids)
                            dap_tids = merged_tids

                        if add_tids or remove_tids:
                            self._log(f"Syncing {pl_name} to Local Library", 'info')
                            self.library.update_playlist({"name": target_pl['name'], "is_favorites": False, "add": add_tids, "remove": remove_tids})
                            target_pl['tracks'] = list(dap_tids)
                    except Exception as e:
                        self._log(
                            f"Error parsing DAP playlist {pl_file.name}: {e}")

        if not manifest:
            is_new_library = True
            self._status('Creating library')
            self._log('Scanning destination', 'dim')
            for track in tracks_list:
                tid = track.get('id')
                rel_path = dest_path_rel(track, conv_kbps)
                if not tid or not rel_path: continue

                full_dst = music_dir / rel_path
                if full_dst.exists():
                    fp = metadata_fp(track, conv_kbps)
                    loc_for_src_fp = track.get('location', '')
                    cur_src_fp = source_fp(loc_for_src_fp) if loc_for_src_fp else ''
                    manifest[tid] = {
                        'dest': str(full_dst.relative_to(dest_root)),
                        'fp': fp,
                        'mdate': track.get('mdate', 0),
                        'src_fp': cur_src_fp
                    }
                    cover_path = full_dst.parent / 'cover.jpg'
                    if cover_path.exists():
                        art_bytes = extract_art_bytes(
                            Path(track.get('location', '')))
                        if art_bytes:
                            manifest[
                                f"art_fp_{track.get('album')}"] = hashlib.md5(
                                    art_bytes).hexdigest()
            self._log(f"   Found {len(manifest)} matching files on disk.",
                      'dim')

        self._log('Analysing library for changes', 'dim')
        self._status('Analysing library for changes')
        self._pct(8)

        new_manifest = {}
        id_to_dst = {}
        id_to_track = {}
        copy_jobs = []
        expected_tids = set()
        album_tracks = {}

        allowed_pl_ids = set()
        if self.sync_prefs.get('sync_mode') == 'filtered':
            selected_pls = self.sync_prefs.get('sel_playlists', [])
            for pl in playlists:
                if pl['name'] in selected_pls:
                    allowed_pl_ids.update(pl['tracks'])

        for track in tracks_list:
            tid = track.get('id', '')
            if tid:
                id_to_track[tid] = track

            if self.sync_prefs.get('sync_mode') == 'filtered':
                has_filters = any([
                    self.sync_prefs.get('sel_artists'),
                    self.sync_prefs.get('sel_albums'),
                    self.sync_prefs.get('sel_genres'),
                    self.sync_prefs.get('sel_playlists')
                ])

                if has_filters:
                    is_selected = (track.get('artist') in self.sync_prefs.get(
                        'sel_artists', []) or track.get('album')
                                   in self.sync_prefs.get('sel_albums', [])
                                   or track.get('genre')
                                   in self.sync_prefs.get('sel_genres', [])
                                   or tid in allowed_pl_ids)
                    if not is_selected:
                        continue

            if not self.sync_prefs.get('videos', False):
                if 'video' in track.get('kind', '').lower():
                    continue

            rel_path = dest_path_rel(track, conv_kbps)
            if not rel_path:
                continue

            dst = music_dir / rel_path
            album_dir = dst.parent
            if album_dir not in album_tracks:
                album_tracks[album_dir] = []
            album_tracks[album_dir].append(track)

            fp = metadata_fp(track, conv_kbps)
            mdate = track.get('mdate', 0)
            old = manifest.get(tid, {})
            old_fp = old.get('fp')
            old_mdate = old.get('mdate', 0)
            old_dst_str = old.get('dest')
            if self.sync_prefs.get('extract_lrc'):
                lrc_dst = dst.with_suffix('.lrc')
                if not lrc_dst.exists():
                    loc = track.get('location', '')
                    if loc:
                        lyrics = extract_lyrics(Path(loc)).strip()
                        if lyrics:
                            lyrics = unicodedata.normalize('NFC', lyrics)
                            try:
                                lrc_dst.parent.mkdir(parents=True,
                                                     exist_ok=True)
                                lrc_dst.write_text(lyrics, encoding='utf-8')
                            except Exception:
                                pass

            dst_rel_norm = unicodedata.normalize('NFC', str(dst.relative_to(dest_root)))
            old_dst_norm = unicodedata.normalize('NFC', old_dst_str) if old_dst_str else None
            fp_match = (old_fp == fp)
            dst_match = (old_dst_norm == dst_rel_norm)
            mdate_changed = (mdate != old_mdate) and (old_mdate != 0)

            loc_for_src_fp = track.get('location', '')
            cur_src_fp = source_fp(loc_for_src_fp) if loc_for_src_fp else ''
            old_src_fp = old.get('src_fp', '')
            
            if old_src_fp:
                src_unchanged = (cur_src_fp == old_src_fp)
            else:
                src_unchanged = not mdate_changed

            if fp_match and dst_match and old_dst_str and dst.exists() and src_unchanged:
                new_manifest[tid] = {
                    'dest': old_dst_str,
                    'fp': fp,
                    'mdate': mdate,
                    'src_fp': cur_src_fp
                }
                id_to_dst[tid] = dst
                expected_tids.add(tid)
                continue

            loc = track.get('location', '')
            if not loc:
                continue

            if old_dst_str:
                old_dst = dest_root / old_dst_str
                if old_dst != dst:
                    try:
                        old_dst.unlink(missing_ok=True)
                    except Exception:
                        pass
            if not old_dst_str and dst.exists():
                new_manifest[tid] = {
                    'dest': str(dst.relative_to(dest_root)),
                    'fp': fp,
                    'mdate': mdate,
                    'src_fp': cur_src_fp
                }
                id_to_dst[tid] = dst
                expected_tids.add(tid)
                continue

            action = 'update' if old_dst_str else 'copy'
            copy_jobs.append({
                'action': action,
                'src': Path(loc),
                'dst': dst,
                'name': track.get('name', '?'),
                'tid': tid,
                'fp': fp,
                'mdate': mdate,
                'target_kbps': conv_kbps,
                'src_bitrate': track.get('bitRate', 0),
                'artist': track.get('artist', ''),
                'albumArtist': track.get('albumArtist', '') or track.get('artist', ''),
                'album': track.get('album', ''),
                'trackNumber': track.get('trackNumber', 0),
                'genre': track.get('genre', ''),
                'composer': track.get('composer', ''),
                'bpm': track.get('bpm', 0),
                'year': track.get('year', 0),
                'src_fp': cur_src_fp
            })
            expected_tids.add(tid)

        copy_jobs.sort(key=lambda x: str(x['dst']))
        if copy_jobs and not dry_run:
            try:
                free = shutil.disk_usage(dest_root).free
                estimated = sum(
                    job['src'].stat().st_size for job in copy_jobs
                    if job['src'].exists()
                )
                if estimated > 0 and free < estimated + 50 * 1024 * 1024:
                    needed_mb = (estimated - free) / (1024 * 1024)
                    import threading as _threading
                    warn_ev = _threading.Event()
                    warn_ok = [False]
                    def _warn():
                        from tkinter import messagebox as _mb
                        msg = (
                            "The destination may not have enough free space.\n\n"
                            f"  Free:      {free / 1024 / 1024:.0f} MB\n"
                            f"  Estimated: {estimated / 1024 / 1024:.0f} MB\n\n"
                            "Continue anyway?"
                        )
                        r = _mb.askyesno("Low Disk Space", msg, icon='warning')
                        warn_ok[0] = r
                        warn_ev.set()
                    self.root.after(0, _warn)
                    warn_ev.wait()
                    if not warn_ok[0]:
                        self._wrap_up_sync(dest_root, manifest_path, new_manifest, playlists,
                                           tracks_list, 0, 0, 0, 0, 0, 0, True, dismissed_unrecognized)
                        return
            except Exception:
                pass
        if dry_run:
            self._log(f"Dry run: {len(copy_jobs)} file(s) would be copied/updated", 'info')
            for job in copy_jobs:
                self._log(f"  [{job['action']}] {job['dst'].relative_to(dest_root)}", 'dim')
            stale_preview = set(manifest.keys()) - expected_tids
            stale_count = sum(1 for t in stale_preview
                              if isinstance(manifest.get(t), dict) and manifest[t].get('dest'))
            if stale_count:
                self._log(f"Dry run: {stale_count} stale file(s) would be removed", 'info')
            self._wrap_up_sync(dest_root, manifest_path, new_manifest, playlists,
                               tracks_list, 0, 0, 0, 0, 0, 0, False, dismissed_unrecognized)
            return
        managed_paths = set()
        for entry in manifest.values():
            if isinstance(entry, dict) and entry.get('dest'):
                managed_paths.add(unicodedata.normalize('NFC', str(dest_root / entry['dest'])))
        for p in id_to_dst.values():
            managed_paths.add(unicodedata.normalize('NFC', str(p)))
        for job in copy_jobs:
            managed_paths.add(unicodedata.normalize('NFC', str(job['dst'])))
                
        unrecognized = []
        if music_dir.exists():
            for f in music_dir.rglob('*'):
                if f.is_file() and f.suffix.lower() in MEDIA_EXTS:
                    if unicodedata.normalize('NFC', str(f)) not in managed_paths:
                        unrecognized.append(f)
                        
        if playlists_dir.exists():
            expected_pl_names = {unicodedata.normalize('NFC', sanitize(p['name']) + '.m3u8') for p in playlists}
            if self.sync_prefs.get('sync_favorites'):
                expected_pl_names.add(unicodedata.normalize('NFC', 'Favorites.m3u8'))
                
            known_old_pls = {unicodedata.normalize('NFC', name + '.m3u8') for name in last_playlist_state.keys()}
            
            for f in playlists_dir.glob('*.m3u8'):
                norm_name = unicodedata.normalize('NFC', f.name)
                if norm_name not in expected_pl_names and norm_name not in known_old_pls:
                    unrecognized.append(f)
        unrecognized = [
            f for f in unrecognized
            if unicodedata.normalize('NFC', str(f)) not in dismissed_unrecognized
        ]

        if unrecognized:
            resolution = self._ask_import_conflict(len(unrecognized))
            if resolution == 'import':
                self._status('Importing new files to Local Library')
                mac_import_dir = Path.home() / 'Music' / 'Nugget Imports'
                mac_import_dir.mkdir(parents=True, exist_ok=True)
                import time
                batch_folder = mac_import_dir / str(int(time.time()))
                batch_folder.mkdir()
                import_paths = []
                for src_file in unrecognized:
                    dst_file = batch_folder / src_file.name
                    counter = 1
                    while dst_file.exists():
                        dst_file = batch_folder / f"{src_file.stem}_{counter}{src_file.suffix}"
                        counter += 1
                    try:
                        shutil.copy2(src_file, dst_file)
                        import_paths.append(str(dst_file))
                        src_file.unlink()
                    except Exception: pass
                if import_paths:
                    self.library.import_paths(import_paths)
                    self._log(f"Imported {len(import_paths)} items to Local Library.", 'ok')
            elif resolution == 'delete':
                for f in unrecognized:
                    try:
                        f.unlink()
                        dismissed_unrecognized.add(unicodedata.normalize('NFC', str(f)))
                    except: pass
            elif resolution == 'ignore':
                for f in unrecognized:
                    dismissed_unrecognized.add(unicodedata.normalize('NFC', str(f)))
        removed = 0
        if self.sync_prefs.get('cleanup', True) and not dry_run:
            self._status('Cleaning up files')
            stale_tids = {k for k in manifest.keys() if not k.startswith('art_fp_')} - expected_tids
            for tid in stale_tids:
                entry = manifest[tid]
                if not isinstance(entry, dict):
                    continue

                stale_rel = entry.get('dest')
                if stale_rel:
                    stale_file = dest_root / stale_rel
                    try:
                        if stale_file.exists():
                            stale_file.unlink()
                            removed += 1
                            self._log(f"Removed {stale_rel}", 'del')
                    except Exception:
                        pass
            if not self.sync_prefs.get('artwork', HAS_MUTAGEN) and music_dir.exists():
                for root_str, _, files in os.walk(music_dir):
                    if 'cover.jpg' in files:
                        try:
                            (Path(root_str) / 'cover.jpg').unlink()
                            removed += 1
                        except Exception: pass

            if not self.sync_prefs.get('extract_lrc', True) and music_dir.exists():
                for root_str, _, files in os.walk(music_dir):
                    for f in files:
                        if f.lower().endswith('.lrc'):
                            try:
                                (Path(root_str) / f).unlink()
                                removed += 1
                            except Exception: pass

            if not self.sync_prefs.get('playlists', True) and playlists_dir.exists():
                try:
                    removed += sum(len(files) for _, _, files in os.walk(playlists_dir))
                    shutil.rmtree(playlists_dir)
                except Exception: pass
            elif not self.sync_prefs.get('sync_favorites', True) and playlists_dir.exists():
                fav_pl = playlists_dir / 'Favorites.m3u8'
                try:
                    if fav_pl.exists():
                        fav_pl.unlink()
                        removed += 1
                except Exception: pass
            for directory in [music_dir, dest_root]:
                if directory.exists():
                    for (root_str, dirs, files) in os.walk(directory, topdown=False):
                        root_path = Path(root_str)
                        if root_path in (dest_root, music_dir, playlists_dir):
                            continue
                        leftovers =[f for f in files if f.lower() in ('cover.jpg', '.ds_store', '.nuglib')]
                        if len(leftovers) == len(files):
                            for f in leftovers:
                                try:
                                    (root_path / f).unlink()
                                except Exception:
                                    pass
                        try:
                            if not any(root_path.iterdir()):
                                root_path.rmdir()
                        except Exception:
                            pass
            if removed > 0:
                self._log(f"{removed} item(s) removed", 'dim')

        self._log(
            f"Syncing now",
            'dim')
        self._pct(10)

        copied = updated = errors = 0
        write_queue = queue.Queue(maxsize=WRITE_QUEUE_MAX)
        write_done_count = 0

        def writer_task():
            nonlocal copied, updated, errors, write_done_count
            while True:
                item = write_queue.get()
                if item is None:
                    write_queue.task_done()
                    break

                if getattr(self, '_cancel_requested', False):
                    write_done_count += 1
                    write_queue.task_done()
                    continue

                status = item.get('status')
                dst, name, tid = item['dst'], item['name'], item['tid']

                if status == 'success':
                    try:
                        new_manifest[tid] = {
                            'dest': str(dst.relative_to(dest_root)),
                            'fp': item['fp'],
                            'mdate': item.get('mdate', 0),
                            'src_fp': item.get('src_fp', '')
                        }
                        id_to_dst[tid] = dst

                        if item['action'] == 'copy':
                            copied += 1
                            self._log(f"Copied {dst.relative_to(dest_root)}", 'ok')
                        else:
                            updated += 1
                            self._log(f"Updated {dst.relative_to(dest_root)}",
                                      'upd')
                    except Exception as exc:
                        errors += 1
                        self._log(f"Update failed for {name} — {exc}", 'del')
                else:
                    errors += 1
                    self._log(f"Copy failed for {name} — {item.get('error')}",
                              'del')

                write_done_count += 1
                if write_done_count % 5 == 0 or write_done_count == len(copy_jobs):
                    self._pct(10 + write_done_count / max(len(copy_jobs), 1) * 65)
                    self._status(f"Copied {write_done_count}/{len(copy_jobs)} tracks")
                write_queue.task_done()

        writer_thread = threading.Thread(target=writer_task, daemon=True)
        writer_thread.start()

        def reader_task(job):
            if getattr(self, '_cancel_requested', False):
                job['status'] = 'cancelled'
                write_queue.put(job)
                return
            try:
                src = job['src']
                dst = job['dst']
                target_kbps = job.get('target_kbps', 0)
                src_bitrate = job.get('src_bitrate', 0)

                if not dry_run:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                if target_kbps > 0 and src_bitrate > target_kbps:
                    with tempfile.NamedTemporaryFile(suffix='.m4a', delete=False) as tmp:
                        tmp_path = tmp.name
                    try:
                        try:
                            if sys.platform == 'darwin':
                                subprocess.run(['afconvert', '-f', 'm4af', '-d', 'aac', '-b', str(target_kbps * 1000), str(src), tmp_path], check=True, capture_output=True)
                            else:
                                subprocess.run(['ffmpeg', '-i', str(src), '-c:a', 'aac', '-b:a', str(target_kbps * 1000), '-vn', '-y', tmp_path], check=True, capture_output=True)
                        except FileNotFoundError:
                            if sys.platform != 'darwin':
                                raise RuntimeError("ffmpeg is required for conversion on Windows.")
                            else:
                                raise
                                
                        if HAS_MUTAGEN:
                            try:
                                tags = MP4(tmp_path)
                                tags['\xa9nam'] = [job.get('name', 'Unknown')]
                                tags['\xa9ART'] = [job.get('artist', 'Unknown')]
                                tags['aART']    = [job.get('albumArtist', job.get('artist', 'Unknown'))]
                                tags['\xa9alb'] = [job.get('album', 'Unknown')]
                                tags['trkn']    = [(int(job.get('trackNumber', 0)), 0)]
                                if job.get('genre'):    tags['\xa9gen'] = [job['genre']]
                                if job.get('composer'): tags['\xa9wrt'] = [job['composer']]
                                if job.get('bpm'):      tags['tmpo']    = [int(job['bpm'])]
                                if job.get('year'):     tags['\xa9day'] = [str(job['year'])]
                                tags.save()
                            except Exception: pass

                        if not dry_run:
                            shutil.copy2(tmp_path, dst)
                    finally:
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass
                else:
                    if not dry_run:
                        shutil.copy2(src, dst)

                job['status'] = 'success'
                write_queue.put(job)
            except Exception as exc:
                job['status'] = 'error'
                job['error'] = str(exc)
                write_queue.put(job)

        with ThreadPoolExecutor(max_workers=READ_WORKERS) as pool:
            for job in copy_jobs:
                pool.submit(reader_task, job)

        if getattr(self, '_cancel_requested', False):
            self._wrap_up_sync(dest_root, manifest_path, new_manifest, playlists, tracks_list, copied, updated, 0, removed, 0, errors, True, dismissed_unrecognized)
            return
        art_saved = art_skipped = 0
        if self.sync_prefs.get('artwork', True) and HAS_MUTAGEN:
            self._pct(75)
            self._status('Checking album art')
            art_jobs = []
            for (album_dir, tracks) in album_tracks.items():
                if not album_dir.exists():
                    continue

                cover_path = album_dir / 'cover.jpg'
                tracks.sort(key=lambda t: (t.get('discNumber') or 1,
                                           t.get('trackNumber') or 1))

                art_bytes = None
                album_name = tracks[0].get('album', 'Unknown')
                for track in tracks:
                    loc = track.get('location', '')
                    src = Path(loc) if loc else None
                    if src and src.exists():
                        art_bytes = extract_art_bytes(src)
                        if art_bytes:
                            break

                if not art_bytes:
                    continue

                current_art_fp = hashlib.md5(art_bytes).hexdigest()
                old_art_fp = manifest.get(f"art_fp_{album_name}")
                if cover_path.exists() and current_art_fp == old_art_fp:
                    new_manifest[f"art_fp_{album_name}"] = current_art_fp
                    art_skipped += 1
                    continue

                art_jobs.append((album_dir, cover_path, art_bytes,
                                 current_art_fp, album_name))

            def do_art(job):
                a_dir, c_path, a_bytes, a_fp, a_name = job
                try:
                    if hasattr(shutil, 'disk_usage'):
                        if shutil.disk_usage(dest_root).free < (
                                2 * 1024 * 1024):
                            return False, a_dir, None, a_name

                    with tempfile.NamedTemporaryFile(delete=False) as tmp:
                        tmp.write(a_bytes)
                        tmp_path = tmp.name
                    with tempfile.NamedTemporaryFile(suffix='.bmp',
                                                     delete=False) as tmp_bmp:
                        bmp_path = tmp_bmp.name

                    success = False
                    if sys.platform == 'darwin':
                        subprocess.run([
                            'sips', '-s', 'format', 'bmp', '-z',
                            str(ART_SIZE),
                            str(ART_SIZE), tmp_path, '--out', bmp_path
                        ], capture_output=True)
                        r = subprocess.run([
                            'sips', '-s', 'format', 'jpeg', bmp_path, '--out',
                            str(c_path)
                        ], capture_output=True)
                        if r.returncode == 0 and c_path.exists():
                            success = True
                    else:
                        try:
                            from PIL import Image
                            with Image.open(tmp_path) as img:
                                img = img.convert('RGB')
                                img = img.resize((ART_SIZE, ART_SIZE))
                                img.save(c_path, 'JPEG')
                            if c_path.exists():
                                success = True
                        except ImportError:
                            try:
                                r = subprocess.run(['ffmpeg', '-i', tmp_path, '-vf', f'scale={ART_SIZE}:{ART_SIZE}', '-vframes', '1', '-y', str(c_path)], capture_output=True)
                                if r.returncode == 0 and c_path.exists():
                                    success = True
                            except Exception:
                                pass

                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    try:
                        os.unlink(bmp_path)
                    except OSError:
                        pass

                    if success:
                        return True, a_dir, a_fp, a_name
                except:
                    pass
                return False, a_dir, None, a_name

            if art_jobs:
                art_done = 0
                with ThreadPoolExecutor(max_workers=ART_WORKERS) as pool:
                    futures = {pool.submit(do_art, job): job for job in art_jobs}
                    for fut in as_completed(futures):
                        if getattr(self, '_cancel_requested', False):
                            break
                        success, a_dir, a_fp, a_name = fut.result()
                        art_done += 1
                        if art_done % 5 == 0 or art_done == len(art_jobs):
                            self._pct(75 +
                                      art_done / max(len(art_jobs), 1) * 15)
                            self._log(
                                f"Cover {art_done} out of {len(art_jobs)} completed")
                        if success:
                            art_saved += 1
                            new_manifest[f"art_fp_{a_name}"] = a_fp
                            self._log(
                                f"{a_dir.relative_to(dest_root)}/cover.jpg",
                                'art')
            self._log(
                f"Saved {art_saved} cover(s), {art_skipped} unchanged",
                'dim')

        if getattr(self, '_cancel_requested', False):
            self._wrap_up_sync(dest_root, manifest_path, new_manifest, playlists, tracks_list, copied, updated, 0, removed, art_saved, errors, True, dismissed_unrecognized)
            return
        if self.sync_prefs.get('sync_favorites'):
            fav_tids = [t['id'] for t in tracks_list if t.get('loved')]
            if fav_tids:
                fav_pl = next(
                    (p for p in playlists if p['name'] == 'Favorites'), None)
                if fav_pl: fav_pl['tracks'] = fav_tids
                else:
                    playlists.append({'name': 'Favorites', 'tracks': fav_tids})
        pl_count = 0
        if self.sync_prefs.get('playlists', True) and playlists:
            self._pct(90)
            self._status('Syncing playlists')
            playlists_dir.mkdir(exist_ok=True)
            for pl in playlists:
                name = pl.get('name', 'Playlist')
                items = pl.get('tracks', [])
                lines = ['#EXTM3U', f"#PLAYLIST:{name}"]
                count = 0
                for tid in items:
                    dst = id_to_dst.get(tid)
                    if dst and dst.exists():
                        rel = os.path.relpath(dst, playlists_dir)
                        track = id_to_track.get(tid, {})
                        artist = unicodedata.normalize(
                            'NFC', str(track.get('artist', 'Unknown Artist')))
                        title = unicodedata.normalize(
                            'NFC', str(track.get('name', dst.stem)))
                        rel = unicodedata.normalize('NFC', rel)

                        lines.append(f"#EXTINF:-1,{artist} - {title}")
                        lines.append(rel)
                        count += 1
                if count == 0:
                    continue
                pl_file = playlists_dir / f"{sanitize(name)}.m3u8"
                new_content = '\n'.join(lines)
                if pl_file.exists() and pl_file.read_text(
                        encoding='utf-8') == new_content:
                    continue
                if not dry_run:
                    pl_file.write_text(new_content, encoding='utf-8')
                pl_count += 1
                self._log(f"Copied {name} ({count} tracks)", 'ok')
            self._log(f"{pl_count} playlist(s) updated", 'dim')

        if getattr(self, '_cancel_requested', False):
            self._wrap_up_sync(dest_root, manifest_path, new_manifest, playlists, tracks_list, copied, updated, pl_count, removed, art_saved, errors, True, dismissed_unrecognized)
            return
        if not dry_run and (copied > 0 or updated > 0 or removed > 0):
            rb_dir = dest_root / '.rockbox'
            if rb_dir.exists():
                try:
                    (rb_dir / 'database_unclean').touch()
                    self._log('The Rockbox database has been marked as rebuild.', 'dim')
                except Exception:
                    pass
        if self.sync_prefs.get('listenbrainz_token') or self.sync_prefs.get('sync_rb_playcounts'):
            self._process_rockbox_log(dest_root, tracks_list)
        if self.sync_prefs.get('backup_rockbox'):
            self._backup_rockbox(dest_root)
        if self.sync_prefs.get('clean_mac'):
            self._clean_mac_files(dest_root)

        self._wrap_up_sync(dest_root, manifest_path, new_manifest, playlists, tracks_list, copied, updated, pl_count, removed, art_saved, errors, False, dismissed_unrecognized)

    def _wrap_up_sync(self, dest_root, manifest_path, new_manifest, playlists, tracks_list, copied, updated, pl_count, removed, art_saved, errors, cancelled, dismissed_unrecognized=None):
        if dismissed_unrecognized is None:
            dismissed_unrecognized = set()
        current_playlist_state = {}
        if playlists:
            for p in playlists:
                current_playlist_state[sanitize(p['name'])] = p.get('tracks', [])
        if self.sync_prefs.get('sync_favorites'):
            fav_tids = [t['id'] for t in tracks_list if t.get('loved')]
            current_playlist_state['Favorites'] = fav_tids
        playlist_mtime_snapshot = {}
        pl_dir = dest_root / 'Playlists'
        if pl_dir.exists():
            for f in pl_dir.glob('*.m3u8'):
                try:
                    playlist_mtime_snapshot[f.stem] = f.stat().st_mtime
                except OSError:
                    pass
        self._save_local_config()
        nuglib_data = {
            'library_data': new_manifest,
            'playlist_state': current_playlist_state,
            'playlist_mtime_snapshot': playlist_mtime_snapshot,
            'dismissed_unrecognized': list(dismissed_unrecognized),
            'sync_preferences': self.sync_prefs,
            'last_sync_timestamp': str(__import__('time').time())
        }
        manifest_path.write_text(json.dumps(nuglib_data, indent=2), encoding='utf-8')
        self._pct(100 if not cancelled else 0)
        self.is_syncing = False
        self.root.title("Nugget Sync")
        
        def _reset_ui():
            self.cancel_btn.pack_forget()
            self.cancel_btn.config(state='normal')
            self.sync_btn.pack(side='right')
            self.sync_btn.config(state='normal')
            self.last_status_msg = ""
            self.header_label.config(text=f'  Sync to {dest_root.name}')
                
            summary = (f"{'Cancelled sync for' if cancelled else 'Synced music library to'} {dest_root.name}\n\n"
                       f"Tracks Copied: {copied}\n"
                       f"Tracks Updated: {updated}\n"
                       f"Playlists Updated: {pl_count}\n"
                       f"Files Removed: {removed}\n"
                       f"Artwork Processed: {art_saved}\n")
            if errors > 0:
                summary += f"\n{errors} items encountered issues during sync."
                
            if not cancelled and sys.platform != 'win32' and str(dest_root).startswith('/Volumes/'):
                summary += "\n\nWould you like to eject the drive?"
                if messagebox.askyesno('Sync Successful', summary):
                    subprocess.run(['diskutil', 'eject', str(dest_root)], capture_output=True)
            elif not cancelled:
                messagebox.showinfo('Sync Successful', summary)
            else:
                messagebox.showinfo('Sync Cancelled', summary)

        self.root.after(100, _reset_ui)

if __name__ == '__main__':
    lock_file_path = Path.home() / '.nugget_sync.lock'
    lock_file = open(lock_file_path, 'w')
    try:
        if sys.platform == 'win32':
            import msvcrt
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.lockf(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        sys.exit(0)

    is_auto = "--auto" in sys.argv

    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    root = Tk()

    if sys.platform == 'win32':
        import tkinter.font as tkfont
        default_font = tkfont.nametofont("TkDefaultFont")
        default_font.configure(family="Segoe UI", size=9)

    style = ttk.Style(root)
    if 'aqua' in style.theme_names():
        style.theme_use('aqua')

    app = SyncApp(root)

    if is_auto:
        target = Path(app.dest_var.get())
        is_correct_drive = target.exists() and (target / MANIFEST_FILE).exists()

        if is_correct_drive and app.sync_prefs.get('auto_launch'):
            root.lift()
            root.attributes('-topmost', True)
            root.after(10, lambda: root.attributes('-topmost', False))
            app._start_sync()
        else:
            root.destroy()
            sys.exit()

    root.mainloop()
