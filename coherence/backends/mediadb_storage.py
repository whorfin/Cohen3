# -*- coding: utf-8 -*-

# Licensed under the MIT license
# http://opensource.org/licenses/mit-license.php

# Copyright 2007, Frank Scholz <coherence@beebits.net>

"""

MediaStore

*** WORK IN PROGRESS ***

A MediaServer with a database backend,
exposes its content in All, Albums and Artists containers.
Serves cover art with the Album object, and keeps references to
the MusicBrainz DB - http://musicbrainz.org/

Should not scan for files, but gets feeded
with proper tagged ones via some import tool
or/and allow imports via the web-UI.

depends on: Axiom - http://divmod.org/trac/wiki/DivmodAxiom
            Epsilon - http://divmod.org/trac/wiki/DivmodEpsilon
            CoversByAmazon - https://coherence.beebits.net/browser/trunk/coherence/extern/covers_by_amazon.py
            libmtag - http://code.google.com/p/libmtag/
            taglib - http://developer.kde.org/~wheeler/taglib.html

"""

import os
import string
import urllib

from urlparse import urlsplit

from axiom import store, item, attributes
from epsilon.extime import Time

import louie

from twisted.python.filepath import FilePath
from coherence.upnp.core import DIDLLite

from coherence.extern.covers_by_amazon import CoverGetter
import libmtag

MEDIA_DB = 'content/media.db'

ROOT_CONTAINER_ID = 0
AUDIO_CONTAINER = 10
AUDIO_ALL_CONTAINER_ID = 11
AUDIO_ARTIST_CONTAINER_ID = 12
AUDIO_ALBUM_CONTAINER_ID = 13

def sanitize(filename):
    badchars = ''.join(set(string.punctuation) - set('-_+.~'))
    f = unicode(filename.lower())
    f = f.replace(unicode(u'ä'),unicode('ae'))
    f = f.replace(unicode(u'ö'),unicode('oe'))
    f = f.replace(unicode(u'ü'),unicode('ue'))
    f = f.replace(unicode(u'ß'),unicode('ss'))
    f = f.replace(badchars, '_')
    return f

from coherence.extern.logger import Logger
log = Logger('MediaStore')

class Container(object):

    def __init__(self, id, parent_id, name, children_callback=None):
        self.id = id
        self.parent_id = parent_id
        self.name = name
        self.mimetype = 'directory'
        self.item = DIDLLite.StorageFolder(id, parent_id,self.name)
        self.update_id = 0
        if children_callback != None:
            self.children = children_callback
        else:
            self.children = []

    def add_child(self, child):
        self.children.append(child)
        self.item.childCount += 1

    def get_children(self,start=0,request_count=0):
        if callable(self.children):
            children = self.children()
        else:
            children = self.children
        if request_count == 0:
            return children[start:]
        else:
            return children[start:request_count]

    def get_child_count(self):
        if callable(self.children):
            return len(self.children())
        else:
            return len(self.children)

    def get_item(self):
        return self.item

    def get_name(self):
        return self.name

    def get_id(self):
        return self.id

class Artist(item.Item):
    """ definition for an artist """

    schemaVersion = 1
    typeName = 'artist'
    mimetype = 'directory'

    name = attributes.text(allowNone=False, indexed=True)
    musicbrainz_id = attributes.text()

    def get_children(self,start=0,request_count=0):
        children = list(self.db.query(Album, Album.artist == self))
        if request_count == 0:
            return self.children[start:]
        else:
            return self.children[start:request_count]

    def get_child_count(self):
        return len(list(self.db.query(Album, Album.artist == self)))

    def get_item(self):
        item = DIDLLite.MusicArtist(self.storeID, parent_id, self.title)
        return item

    def get_id(self):
        return self.storeID + 1000

    def get_name(self):
        return self.name

    def __repr__(self):
        return '<Artist %d name="%s" musicbrainz="%s">' \
               % (self.storeID, self.name.encode('ascii', 'ignore'), self.musicbrainz_id)

class Album(item.Item):
    """ definition for an album """

    schemaVersion = 1
    typeName = 'album'
    mimetype = 'directory'

    title = attributes.text(allowNone=False, indexed=True)
    musicbrainz_id = attributes.text()
    artist = attributes.reference(allowNone=False, indexed=True)
    cd_count = attributes.integer(default=1)
    cover = attributes.text(default=u'')

    def get_children(self,start=0,request_count=0):
        children = list(self.store.query(Track, Track.album == self))
        if request_count == 0:
            return children[start:]
        else:
            return children[start:request_count]

    def get_child_count(self):
        return len(list(self.store.query(Track, Track.album == self)))

    def get_item(self):
        item = DIDLLite.MusicAlbum(self.storeID+1000, AUDIO_ALBUM_CONTAINER_ID, self.title)
        return item

    def get_id(self):
        return self.storeID + 1000

    def get_name(self):
        return self.title

    def get_cover(self):
        return self.cover

    def __repr__(self):
        return '<Album %d title="%s" artist="%s" #cds %d cover="%s" musicbrainz="%s">' \
               % (self.storeID, self.title.encode('ascii', 'ignore'),
                  self.artist.name.encode('ascii', 'ignore'),
                  self.cd_count,
                  self.cover.encode('ascii', 'ignore'),
                  self.musicbrainz_id)

class Track(item.Item):
    """ definition for a track """

    schemaVersion = 1
    typeName = 'track'

    title = attributes.text(allowNone=False, indexed=True)
    track_nr = attributes.integer(default=1,allowNone=False)
    cd_nr = attributes.integer(default=1,allowNone=False)
    album = attributes.reference(allowNone=False, indexed=True)
    location = attributes.text(allowNone=False)
    rating=attributes.integer(default=0,allowNone=False)
    last_played=attributes.timestamp()
    added=attributes.timestamp(default=Time(),allowNone=False)

    def get_children(self,start=0,request_count=0):
        return []

    def get_child_count(self):
        return 0

    def get_item(self):
        item = DIDLLite.MusicTrack(self.storeID+1000, self.album.storeID+1000,self.title)
        item.artist = self.album.artist.name
        item.album = self.album.title
        if self.album.cover != '':
            item.albumArtURI = self.store.urlbase + str(self.storeID+1000) + '/?cover'
        item.res = []

        _,host_port,_,_,_ = urlsplit(self.store.urlbase)
        if host_port.find(':') != -1:
            host,port = tuple(host_port.split(':'))
        else:
            host = host_port

        _,ext =  os.path.splitext(self.location)
        if ext == '.ogg':
            mimetype = 'application/ogg'
        else:
            mimetype = 'audio/mpeg'

        statinfo = os.stat(self.location)

        res = DIDLLite.Resource('file://'+self.location, 'internal:%s:%s:*' % (host,mimetype))
        try:
            res.size = statinfo.st_size
        except:
            res.size = 0
        item.res.append(res)

        url = self.store.urlbase + str(self.storeID+1000)

        res = DIDLLite.Resource(url, 'http-get:*:%s:*' % mimetype)
        try:
            res.size = statinfo.st_size
        except:
            res.size = 0
        item.res.append(res)

        try:
            # FIXME: getmtime is deprecated in Twisted 2.6
            item.date = datetime.fromtimestamp(statinfo.st_mtime)
        except:
            item.date = None

        return item

    def get_path(self):
        return self.location

    def get_id(self):
        return self.storeID + 1000

    def get_name(self):
        return self.title

    def get_url(self):
        return self.store.urlbase + str(self.storeID+1000)

    def get_cover(self):
        return self.album.cover

    def __repr__(self):
        return '<Track %d title="%s" nr="%d" album="%s" artist="%s" path="%s">' \
               % (self.storeID, self.title.encode('ascii', 'ignore'),
                  self.track_nr,
                  self.album.title.encode('ascii', 'ignore'),
                  self.album.artist.name.encode('ascii', 'ignore'),
                  self.location.encode('ascii', 'ignore'))

class Playlist(item.Item):
    """ definition for a playlist

        - has a name
        - and references to tracks
        - that reference list must keep its ordering
           and items can be inserted at any place,
           moved up or down or deleted
    """

    schemaVersion = 1
    typeName = ''

    name = attributes.text(allowNone=False, indexed=True)
    # references to tracks

class MediaStore(object):

    implements = ['MediaServer']

    def __init__(self, server, **kwargs):
        log.info("MediaStore __init__")
        self.server = server
        self.update_id = 0

        self.medialocation = kwargs.get('medialocation','content/audio')
        self.coverlocation = kwargs.get('coverlocation','content/covers')
        if self.coverlocation[-1] != '/':
            self.coverlocation = self.coverlocation + '/'
        self.mediadb = kwargs.get('mediadb',MEDIA_DB)

        self.name = kwargs.get('name','MediaStore')

        self.urlbase = kwargs.get('urlbase','')
        if( len(self.urlbase)>0 and
            self.urlbase[len(self.urlbase)-1] != '/'):
            self.urlbase += '/'

        self.containers = {}
        self.containers[ROOT_CONTAINER_ID] = \
                Container( ROOT_CONTAINER_ID,-1, self.name)
        self.containers[AUDIO_ALBUM_CONTAINER_ID] = \
                Container( AUDIO_ALBUM_CONTAINER_ID,ROOT_CONTAINER_ID, 'Albums',
                          children_callback=lambda :list(self.db.query(Album,sort=Album.title.ascending)))
        self.containers[ROOT_CONTAINER_ID].add_child(self.containers[AUDIO_ALBUM_CONTAINER_ID])

        louie.send('Coherence.UPnP.Backend.init_completed', None, backend=self)

    def walk(self, path):
        if os.path.exists(path):
            for filename in os.listdir(path):
                if os.path.isdir(os.path.join(path,filename)):
                    self.walk(os.path.join(path,filename))
                else:
                    _,ext =  os.path.splitext(filename)
                    if ext.lower()[1:] in ['mp3','ogg']:
                        self.filelist.append(os.path.join(path,filename))

    def get_music_files(self, musiclocation):
        if not isinstance(musiclocation, list):
            musiclocation = [musiclocation]
        self.filelist = []
        for path in musiclocation:
            self.walk(path)

        for file in self.filelist:
            audio_file = libmtag.File(file)
            album=audio_file.tag().get('album').strip()
            artist=audio_file.tag().get('artist').strip()
            title=audio_file.tag().get('title').strip()
            track=audio_file.tag().get('track')

            if len(artist) == 0:
                continue;
                artist = u'UNKNOWN_ARTIST'
            if len(album) == 0:
                continue;
                album = u'UNKNOWN_ALBUM'
            if len(title) == 0:
                continue;
                title = u'UNKNOWN_TITLE'

            #print "Tags:", album, artist, file

            artist_ds = self.db.findOrCreate(Artist, name=unicode(artist,'utf8'))
            album_ds = self.db.findOrCreate(Album,
                                            title=unicode(album,'utf8'),
                                            artist=artist_ds)
            track_ds = self.db.findOrCreate(Track,
                                            title=unicode(title,'utf8'),
                                            track_nr=int(track),
                                            album=album_ds,
                                            location=unicode(file,'utf8'))

    def show_db(self):
        for album in list(self.db.query(Album,sort=Album.title.ascending)):
            print album
            for track in list(self.db.query(Track, Track.album == album,sort=Track.track_nr.ascending)):
                print track

    def show_albums(self):
        for album in list(self.db.query(Album,sort=Album.title.ascending)):
            print album

    def show_artists(self):
        for artist in list(self.db.query(Artist,sort=Artist.name.ascending)):
            print artist

    def show_tracks_by_artist(self, artist_name):
        artist = self.db.query(Artist,Artist.name == artist_name)
        artist = list(artist)[0]
        for album in list(self.db.query(Album, Album.artist == artist)):
            for track in list(self.db.query(Track, Track.album == album,sort=Track.title.ascending)):
                print track

    def show_tracks_by_title(self, title_or_part):
            for track in list(self.db.query(Track, Track.title.like(u'%',title_or_part,u'%'),sort=Track.title.ascending)):
                print track

    def show_tracks_to_filename(self, title_or_part):
        for track in list(self.db.query(Track, Track.title.like(u'%',title_or_part,u'%'),sort=Track.title.ascending)):
            print track.title, track.album.artist.name, track.track_nr
            _,ext = os.path.splitext(track.path)
            f = "%02d - %s - %s%s" % ( track.track_nr, track.album.artist.name,
                                       track.title, ext)
            f = sanitize(f)
            print f

    def get_album_covers(self):
        for album in list(self.db.query(Album, Album.cover == u'')):
            print "missing cover for:", album.artist.name, album.title
            filename = "%s - %s" % ( album.artist.name, album.title)
            filename = sanitize(filename)

            cover_path = os.path.join(self.coverlocation,filename +'.png')
            if os.path.exists(cover_path) is True:
                print "cover found:", cover_path
                album.cover = cover_path
            else:
                def got_it(f,a):
                    print "cover saved:",f, a.title
                    a.cover = f

                CoverGetter(cover_path,
                            callback=(got_it,(album)),
                            artist=album.artist.name,
                            title=album.title)

    def get_by_id(self,id):
        id = int(id)
        log.info("get_by_id %d" % id)
        item = None
        try:
            item = self.containers[id]
        except:
            try:
                item = self.db.getItemByID(id-1000)
            except:
                pass
        log.info("found", item)
        print "get_by_id found", item
        return item

    def upnp_init(self):
        db_is_new = False
        if os.path.exists(self.mediadb) is False:
            db_is_new = True
        self.db = store.Store(self.mediadb)
        self.db.urlbase = self.urlbase

        if db_is_new is True:
            self.get_music_files(self.medialocation)
            self.get_album_covers()
        #self.show_db()
        #self.show_artists()
        #self.show_albums()
        #self.show_tracks_by_artist(u'Meat Loaf')
        #self.show_tracks_by_artist(u'Beyonce')
        #self.show_tracks_by_title(u'Bad')
        #self.show_tracks_to_filename(u'säen')


        self.current_connection_id = None
        if self.server:
            self.server.connection_manager_server.set_variable(0, 'SourceProtocolInfo',
                        ['internal:%s:audio/mpeg:*' % self.server.coherence.hostname,
                         'http-get:*:audio/mpeg:*',
                         'internal:%s:application/ogg:*' % self.server.coherence.hostname,
                         'http-get:*:application/ogg:*'],
                        default=True)


if __name__ == '__main__':
    from twisted.internet import reactor
    from twisted.internet import task

    reactor.callWhenRunning(MediaStore, None,
                                        medialocation='/data/audio/music',
                                        coverlocation='/data/audio/covers',
                                        mediadb='/tmp/media.db')
    reactor.run()
