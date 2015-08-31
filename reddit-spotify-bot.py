import time
import pytz
import praw
import spotify
import pprint
import threading
import ConfigParser
import MySQLdb
import traceback
import datetime
import tzlocal

from praw.errors import ExceptionList, APIException, RateLimitExceeded

# Read the config file
config = ConfigParser.ConfigParser()
config.read("config.txt")

# Set our Reddit Bot Account and Spotify Account variables
reddit_user = config.get("Reddit", "username")
reddit_pw = config.get("Reddit", "password")

spotify_user = config.get("Spotify", "username")
spotify_pw = config.get("Spotify", "password")

# Connect to our database
db_user = config.get("SQL", "username")
db_pw = config.get("SQL", "password")

db = MySQLdb.connect(host="localhost", user=db_user, passwd=db_pw, db="reddit")

# Subreddits to look for
subreddits = "SpotifyBot+AskReddit+Music"

# define a few template messages
msg_created = (
	"Greetings from the SpotifyBot!"
	"\n\nBased on your comments, "
	"I think you requested a Spotify Playlist to be created."
	"This playlist has been auto-generated for you:"
	"\n\n{playlist}"
	)

msg_pm_created = (
	"Greetings from the SpotifyBot!"
	"\n\nI think you requested a Spotify Playlist to be created, "
	"based on your comments in this submission:\n\n {submission}"
	"\n\nThis playlist has been auto-generated for you:"
	"\n\n{playlist}"
	)

msg_already_created = (
	"Greetings from the SpotifyBot!"
	"\n\nI think you requested a Spotify Playlist to be created, "
	"based on your comments.  A playlist has already been created here:"
	"\n\n{playlist}"
	)

msg_pm_already_created = (
	"Greetings from the SpotifyBot!"
	"\n\nI think you requested a Spotify Playlist to be created, "
	"based on your comments in this submission:\n\n {submission}"
	"\n\nA playlist has already been created here:"
	"\n\n{playlist}"
	)

msg_pm_no_tracks = (
	"Greetings from the SpotifyBot!"
	"\n\nI think you requested a Spotify Playlist to be created, "
	"based on your comments in this submission:\n\n {submission}"
	"\n\nUnfortunately, I could not find any valid tracks from the top-level comments!"
	)
	
msg_no_tracks = (
	"Greetings from the SpotifyBot!"
	"\n\nI think you requested a Spotify Playlist to be created, "
	"based on your comments.  "
	"Unfortunately, I could not find any valid tracks from the top-level comments!"
	)
	

def login(session, username, password):
	logged_in_event = threading.Event()

	def logged_in_listener(session, error_type):
		logged_in_event.set()

	session.on(spotify.SessionEvent.LOGGED_IN, logged_in_listener)
	session.login(username, password)

	while session.connection.state != spotify.ConnectionState.LOGGED_IN:
		time.sleep(0.1)

def logout(session):
    logged_out_event = threading.Event()

    def logged_out_listener(session):
	logged_out_event.set()

    session.on(spotify.SessionEvent.LOGGED_OUT, logged_out_listener)
    session.logout()

    logged_out_event.wait(10)

def append_submission_to_db(submission, playlist):
	db_cursor = db.cursor()
	cmd = "insert into Submissions (submission_url, playlist_url) values(%s, %s)"
	db_cursor.execute(cmd, [submission.url, playlist])
	db.commit()
	db_cursor.close()

def get_submission_playlist(submission_url):
	db_cursor = db.cursor()
	query = "select submission_url, playlist_url from Submissions where submission_url=%s"
	db_cursor.execute(query, [submission_url])
	data = db_cursor.fetchall()
	db_cursor.close()

	if len(data) == 0:
		return None
	else:
		for row in data:
			# should only be one row returned
			return row[1]
		
def append_comment_to_db(comment_id):
	db_cursor = db.cursor()
	cmd = "insert into Comments (comment_id) values(%s)"
	db_cursor.execute(cmd, [comment_id])
        db.commit()
	db_cursor.close()

def has_commented(comment_id):
	db_cursor = db.cursor()
	query = "select comment_id from Comments where comment_id = %s"
	db_cursor.execute(query, [comment_id])
	data = db_cursor.fetchall()
	db_cursor.close()

	if len(data) == 0:
		return False
	else:
		return True

def try_track(session, artist, track):
	try:
		if artist and track:
			search_str = "artist:" + artist + " track:" + track
			search = session.search(search_str)
			search.load()
			if search.track_total > 0:
				# just take the first track it found
				t = search.tracks[0]
				t.load()
				return t
		else:
			search_str = "track:" + track
			search = session.search(search_str)
			search.load()
			if search.track_total > 0:
				# just take the first track it found
				t = search.tracks[0]
				t.load()
				return t
	except Exception as err:
		print "Error trying track.."
		return None

def parse_track(session, line):
	# First look for "by" to see if it's in the format "track by artist"
	if (" by " in line):
		track = line.split(" by ")[0].strip()
		artist = line.split(" by ")[1].strip()

		t = try_track(session, artist, track)
		if t:
			return t

		t = try_track(session, artist, track + "*")
		if t:
			return t

		tokens = artist.split()
		for i in range(1, len(tokens)):
			s = " "
			t = try_track(session, s.join(tokens[:-1 * i]), track)
			if t:
				return t

	# sometimes we'll see:  "cover of" in the description, which isn't part of the actual name
	if (" cover of " in line):
		artist = line.split(" cover of ")[0].strip()
		track = line.split(" cover of ")[1].strip()

		t = try_track(session, artist, track)
		if t:
			return t

		t = try_track(session, artist, track + "*")
		if t:
			return t

		tokens = artist.split()
		for i in range(1, len(tokens)):
			s = " "
			t = try_track(session, s.join(tokens[:-1 * i]), track)
			if t:
				return t

	# a comment format is:  "artist - track", or "track - artist"
	if ("-" in line):
		track = line.split("-")[0].strip()
		artist = line.split("-")[1].strip()

		t = try_track(session, artist, track)
		if t:
			return t

		t = try_track(session, artist, track + "*")
		if t:
			return t

		tokens = artist.split()
		for i in range(1, len(tokens)):
			s = " "
			t = try_track(session, s.join(tokens[:-1 * i]), track)
			if t:
				return t



		t = try_track(session, track, artist)
		if t:
			return t

		t = try_track(session, track, artist + "*")
		if t:
			return t

		tokens = track.split()
		for i in range(1, len(tokens)):
			s = " "
			t = try_track(session, s.join(tokens[:-1 * i]), artist)
			if t:
				return t

	# when all else fails, just try to look up "track"
	t = try_track(session, None, line.strip())
	if t:
		return t

	return None

def find_tracks(session, submission):
	tracks = {}

	for track_comment in submission.comments:
		if isinstance(track_comment, praw.objects.MoreComments):
			continue

		for line in track_comment.body.split('\n'):
			if (not line):
				continue
			track = parse_track(session, line)
			if (track):
				if not track.link.uri in tracks:
					tracks[track.link.uri] = track

	return tracks

def populate_playlist(playlist, tracks):
	for t in tracks:
		track = tracks[t]

		try:
			print("  * Adding " + track.name + " by " + track.artists[0].name + " [" + track.link.uri + "]")
			playlist.add_tracks(track)
		except Exception as err:
			print "Error adding track"
			print err

def create_playlist(session, title):
	reddit_index = -1
	index = 1
	session.playlist_container.load()

	for p in session.playlist_container:
		if isinstance(p, spotify.PlaylistFolder):
			if "Reddit" == p.name:
				reddit_index = index
		index += 1

	if reddit_index > -1:
		new_playlist = session.playlist_container.add_new_playlist(title, reddit_index)
		return new_playlist
	else:
		print "Unable to find [Reddit] folder inside of playlists!"

	return None

def comment_wants_playlist(body):
	if len(body.split('\n')) > 3:
		# Skipping wall of text
		return False

	if ("create" in body or "make" in body or "making" in body) and "spotify" in body and "playlist" in body:
		return True
	elif "SpotifyBot!" in body:
		return True

	return False

def should_private_reply(submission, comment):
	# the jerks over in r/Music don't like bots to post
	if submission.subreddit.display_name.lower() == "music":
		return True
	
	return False

def update_existing_playlist(session, list_url, comment):
	if len(comment.body.split('\n')) > 5:
		# Skipping wall of text
		return False

	link = session.get_link(list_url)
	if not link:
		print "Could no longer find link"
		return False

	playlist = session.get_playlist(link.uri)
	if not playlist:
		print "Could no longer find playlist"
		return False
	playlist.load()

	tracks = playlist.tracks

	for line in comment.body.split('\n'):
		if not line:
			continue
		track = parse_track(session, line)
		if track:
			print "Updating existing playlist " + list_url
			found = False
			for t in tracks:
				t.load()
				if track.link.uri == t.link.uri:
					found = True
					break
			if found == False:
				print "Found new track, adding " + track.link.uri
				playlist.add_tracks(track)

def create_new_playlist(reddit, session, submission, comment):

	tracks = find_tracks(session, submission)
	num_tracks = len(tracks)
	print("Found " + str(num_tracks) + " tracks for new playlist")

	if num_tracks > 0:
		# add a new playlist
		new_playlist = create_playlist(session, submission.title)
		if new_playlist:
			print("New playlist: " + new_playlist.link.url)

			populate_playlist(new_playlist, tracks)

			if should_private_reply(submission, comment):
				reddit.send_message(comment.author.name, "Spotify Playlist", msg_pm_created.format(submission=submission.url, playlist=new_playlist.link.url))
			else:
				comment.reply(msg_created.format(playlist=new_playlist.link.url))
		append_comment_to_db(comment.id)
		append_submission_to_db(submission, new_playlist.link.url)
		print("comment and submission recorded in journal")
	else:
		if should_private_reply(submission, comment):
			reddit.send_message(comment.author.name, "Spotify Playlist", msg_pm_no_tracks.format(submission=submission.url))
		else:
			comment.reply(msg_no_tracks)
		append_comment_to_db(comment.id)
		print("comment recorded in journal")

def process_comment(reddit, spotify_session, comment):

	# calculate how far back in the queue we currently are
	timestamp = datetime.datetime.utcfromtimestamp(comment.created_utc)
	timestamp_now = datetime.datetime.utcnow()
	diff = (timestamp_now - timestamp)

	print "Processing comment id=" + comment.id + ", user=" + comment.author.name + ", time_ago=" + str(diff)

	# fetch the submission/playlist and check if it's in our database already
	playlist_url = get_submission_playlist(comment.submission.url)
	if playlist_url:
		# it's in our database, so see if this is another request, or another track
		print "Submission already recorded, checking comments"
		if comment_wants_playlist(comment.body):
			print "Sending existing playlist: " + playlist_url + " to " + comment.author.name
			submission = reddit.get_submission(comment.submission.url)
			if should_private_reply(submission, comment):
				reddit.send_message(comment.author.name, "Spotify Playlist", msg_pm_already_created.format(submission=submission.url, playlist=playlist_url))
			else:
				comment.reply(msg_already_created.format(playlist=playlist_url))

			append_comment_to_db(comment.id)
		elif comment.is_root:
			# already processed this submission, but perhaps this is a new track to add
			print("\n------- Update Playlist ----------------")

			login(spotify_session, spotify_user, spotify_pw)
			try:
				update_existing_playlist(spotify_session, playlist_url, comment)
				append_comment_to_db(comment.id)
			except Exception as err:
				print "Error updating playlist"
				print err

			logout(spotify_session)
		else:
			print "Not a request for playlist, but also not a top-level comment"

	else:
		# it's not in our database, so see if they are requesting a playlist
		if comment_wants_playlist(comment.body):
			submission = reddit.get_submission(comment.submission.url)

			print("\n------- Create Playlist ------------------")
			login(spotify_session, spotify_user, spotify_pw)
			try:
				create_new_playlist(reddit, spotify_session, submission, comment)
			except Exception as err:
				print "Error creating new playlist"
				print err

			logout(spotify_session)

def main():

	spotify_session = spotify.Session()
	loop = spotify.EventLoop(spotify_session)
	loop.start()

	reddit = praw.Reddit('Spotify Playlist B0t v1.0')
	reddit.login(reddit_user, reddit_pw, disable_warning=True)

	while True:
		print "Looking for comments..."
		try:
			for comment in praw.helpers.comment_stream(reddit, subreddits, limit=None, verbosity=0):
				# skip comments we have already processed in our database
				if has_commented(comment.id):
					print("Already processed this comment, ignoring..")
					continue

				# make sure user hasn't been deleted
				if not comment.author:
					continue

				# make sure this comment isn't us!
				if comment.author.name == reddit_user:
					continue

				try:
					# go ahead and attempt to process this comment
					process_comment(reddit, spotify_session, comment)
				except Exception as err:
					print err
					print traceback.format_exc()

		except Exception as err2:
			print "Error in main loop"
			print err2
			print traceback.format_exc()
			time.sleep(5)

if __name__ == '__main__':
	main()
