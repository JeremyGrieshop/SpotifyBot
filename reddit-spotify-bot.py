import time
import praw
import spotify
import pprint
import threading
import ConfigParser
#import tinyurl

from praw.errors import ExceptionList, APIException, RateLimitExceeded

# Read the config file
config = ConfigParser.ConfigParser()
config.read("config.txt")

# Set our Reddit Bot Account and Spotify Account variables
reddit_user = config.get("Reddit", "username")
reddit_pw = config.get("Reddit", "password")
spotify_user = config.get("Spotify", "username")
spotify_pw = config.get("Spotify", "password")

# Subreddits to look for
subreddits = "SpotifyBot+AskReddit+Music"

# define a few template messages
msg_created = (
	"Greetings from the SpotifyBot!"
	"\n\nBased on your comments, "
	"I think you requested a Spotify Playlist to be created."
	"This playlist has been auto-generated for you:"
	"\n{playlist}"
	)

msg_pm_created = (
	"Greetings from the SpotifyBot!"
	"\n\nI think you requested a Spotify Playlist to be created, "
	"based on your comments in this submission:\n {submission}"
	"\n\nThis playlist has been auto-generated for you:"
	"\n{playlist}"
	)

msg_already_created = (
	"Greetings from the SpotifyBot!"
	"\n\nI think you requested a Spotify Playlist to be created, "
	"based on your comments.  A playlist has already been created here:"
	"\n{playlist}"
	)

msg_pm_already_created = (
	"Greetings from the SpotifyBot!"
	"\n\nI think you requested a Spotify Playlist to be created, "
	"based on your comments in this submission:\n {submission}"
	"\n\nA playlist has already been created here:"
	"\n{playlist}"
	)

msg_pm_no_tracks = (
	"Greetings from the SpotifyBot!"
	"\n\nI think you requested a Spotify Playlist to be created, "
	"based on your comments in this submission:\n {submission}"
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

def append_submission_to_file(submission, playlist):
	with open("submissions.txt", "a") as myfile:
		myfile.write(submission.url + " " + playlist)
		myfile.write("\n")
		myfile.close()

def get_submission(submission):
	submissions = open("submissions.txt").read().splitlines()
	for line in submissions:
		if submission.url in line:
			return line.split()[1]
	return None

def append_comment_to_file(comment_id):
	with open("comments.txt", "a") as myfile:
		myfile.write(comment_id)
		myfile.write("\n")
		myfile.close()

def has_commented(comment_id):
	comments = open("comments.txt").read().splitlines()
	if comment_id in comments:
		return True
	else:
		return False

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
		print "Skipping wall of text."
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

def main():

	session = spotify.Session()
	loop = spotify.EventLoop(session)
	loop.start()

	r = praw.Reddit('Spotify Playlist B0t v1.0')
	r.login(reddit_user, reddit_pw, disable_warning=True)

	while True:
		print "Looking for comments..."
		try:
			for comment in praw.helpers.comment_stream(r, subreddits, limit=None, verbosity=0):
				if has_commented(comment.id):
					print("Already processed this comment, ignoring..")
					continue

				# make sure this comment isn't us!
				if comment.author.name == reddit_user:
					continue

				if comment_wants_playlist(comment.body):
					print("\n--------------------------------")
				else:
					continue

				submission = r.get_submission(comment.submission.url)
				s = get_submission(submission)
				if s:
					print("Submission already processed: " + s)
					if should_private_reply(submission, comment):
						r.send_message(comment.author.name, "Spotify Playlist", msg_pm_already_created.format(submission=submission.url, playlist=s))
					else:
						comment.reply(msg_already_created.format(playlist=s))

					append_comment_to_file(comment.id)
					continue

				print("Preparing to create a playlist for " + comment.permalink)

				print("Logging into spotify..")
				login(session, spotify_user, spotify_pw)

				print("Looking for tracks..")
				tracks = find_tracks(session, submission)
				num_tracks = len(tracks)
				print("Found " + str(num_tracks) + " tracks")

				if num_tracks > 0:
					# add a new playlist
					new_playlist = create_playlist(session, submission.title)
					if new_playlist:
						print("New playlist: " + new_playlist.link.url)

						populate_playlist(new_playlist, tracks)
						print("new playlist populated for " + comment.author.name)

						logout(session)

						if should_private_reply(submission, comment):
							r.send_message(comment.author.name, "Spotify Playlist", msg_pm_created.format(submission=submission.url, playlist=new_playlist.link.url))
						else:
							comment.reply(msg_created.format(playlist=new_playlist.link.url))
					append_comment_to_file(comment.id)
					append_submission_to_file(submission, new_playlist.link.url)
					print("comment and submission recorded in journal")
				else:
					logout(session)

					if should_private_reply(submission, comment):
						r.send_message(comment.author.name, "Spotify Playlist", msg_pm_no_tracks.format(submission=submission.url))
					else:
						comment.reply(msg_no_tracks)
					append_comment_to_file(comment.id)
					print("comment recorded in journal")

		except Exception as err:
			print "Error in main loop"
			logout(session)
			print err
			time.sleep(5)

if __name__ == '__main__':
	main()
