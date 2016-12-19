#!/usr/bin/python

import time
import pytz
import praw
import logging
import spotipy
import spotipy.util as util
from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth
import pprint
import threading
import ConfigParser
import MySQLdb
import traceback
import datetime
import tzlocal
import lxml
import urllib
import sys
import threading
from fuzzywuzzy import fuzz
from fuzzywuzzy import process
import re
import itertools
from requests.exceptions import ReadTimeout, ConnectionError

from lxml import etree
from praw.exceptions import APIException, ClientException, PRAWException

# Read the config file
config = ConfigParser.ConfigParser()
config.read("config.txt")

# Set our Reddit Bot Account
reddit_user = config.get("Reddit", "username")
reddit_pw = config.get("Reddit", "password")
reddit_client_id = config.get("Reddit", "reddit_client_id")
reddit_client_secret = config.get("Reddit", "reddit_client_secret")

# Set our Spotify Account variables
spotipy_username = config.get("Spotify", "spotipy_username")
spotipy_client_id = config.get("Spotify", "spotipy_client_id")
spotipy_client_secret = config.get("Spotify", "spotipy_client_secret")
spotipy_redirect_uri = config.get("Spotify", "spotipy_redirect_uri")

sp_oauth = SpotifyOAuth(spotipy_client_id,
                     spotipy_client_secret,
                     spotipy_redirect_uri,
                     scope='playlist-modify-public',
                     cache_path='.cache-' + spotipy_username)

# Connect to our database
db_user = config.get("SQL", "username")
db_pw = config.get("SQL", "password")
db_database = config.get("SQL", "database")

db = MySQLdb.connect(host="localhost", user=db_user, passwd=db_pw, db=db_database, charset='utf8')

# global spotify variables for login
spotify = None
token = None
token_info = None

# Subreddits to look for
subreddits = "SpotifyBot+IndieHeads+listentothis+Music+AskReddit"
#subreddits = "SpotifyBot"

log_level = 2

# global playlists for quick lookups
playlists = {}

# define a few template messages
msg_created = (
	"Greetings from the SpotifyBot!"
	"\n\nBased on your comments, "
	"I think you requested a Spotify Playlist to be created. "
	"This playlist has been auto-generated for you:"
	"\n\n{playlist}"
	)

msg_pm_created = (
	"Greetings from the SpotifyBot!"
	"\n\nI think you requested a Spotify Playlist to be created, "
	"based on your [comment]({comment}) in this submission:\n\n {submission}"
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
	"based on your [comment]({comment}) in this submission:\n\n {submission}"
	"\n\nA playlist has already been created here:"
	"\n\n{playlist}"
	)

msg_pm_no_tracks = (
	"Greetings from the SpotifyBot!"
	"\n\nI think you requested a Spotify Playlist to be created, "
	"based on your [comment]({comment}) in this submission:\n\n {submission}"
	"\n\nUnfortunately, I could not find enough tracks from the top-level comments!"
	)
	
msg_no_tracks = (
	"Greetings from the SpotifyBot!"
	"\n\nI think you requested a Spotify Playlist to be created, "
	"based on your comments.  "
	"Unfortunately, I could not find any valid tracks from the top-level comments!"
	)


def log(message, level):
	if level <= log_level:
		print "[" + time.strftime("%c") + "] " + message.encode('utf-8')

def append_submission_to_db(submission, playlist):
	db_cursor = db.cursor()
	cmd = "insert into Submissions (submission_url, playlist_url) values(%s, %s)"
	db_cursor.execute(cmd, [submission.url, playlist])
	db.commit()
	db_cursor.close()

def load_submission_playlists():
        global playlists

        db_cursor = db.cursor()
	query = "select submission_url, playlist_url from Submissions"
	db_cursor.execute(query)
	data = db_cursor.fetchall()
	db_cursor.close()

        playlists = {}
	if len(data) > 0:
		for row in data:
			playlists[row[0]] = row[1]

def get_submission_playlist(submission_url):
	global playlists

	if submission_url in playlists:
		return playlists[submission_url]
	else:
		return None

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

def parse_youtube_link(link):
	url = urllib.urlopen(link)
	if url:
		youtube = etree.HTML(url.read())
		title = youtube.xpath("//span[@id='eow-title']/@title")
		if title:
			track = parse_track(''.join(title))
			return track

def parse_comment(line):

	# see if it's a reddit link format: [fdsfsd](link)
	expr = re.compile("\[(.+)\]\((.+)\)")
	match = expr.search(line)
	if match:
		line = match.group(1)

	# see if we have a youtube link
	if ("www.youtube.com/" in line or "www.youtu.be/" in line):
		for word in line.split():
			if ("www.youtube.com/" in word or "www.youtu.be/" in word):
				if "(" in word:
					t = parse_youtube_link(
						word.split("(",1)[1].split(")",1)[0])
					return t
				else:
					t = parse_youtube_link(word)
					return t


	# if the line ends with a period, remove it
	if line.endswith('.'):
		line = line[:-1]

	# if the line starts with *, remove it
	if line.startswith('*'):
		line = line[1:]

	line = line.strip()

	# naively try the whole thing first
	track = parse_track(line)
	if track:
		return track

	# divide and conquer.  First, break it up into sentences
	if "." in line:
		for sentence in line.split("."):
			track = parse_track(sentence)
			if track:
				return track

	# now try the comma
	if "," in line:
		for sentence in line.split(","):
			track = parse_track(sentence)
			if track:
				return track

	# just give up, too many searches to perform
	if len(line.split(" ")) > 8:
		return None

	# oh boy, now we are going to have to break it apart one-by-one
	line = " ".join(line.split(" ")[:-1])
	while line:
		track = parse_track(line)
		if track:
			return track

		line = " ".join(line.split(" ")[:-1])

	# fail
	return None

def parse_track(line):

	search_text = line
	if search_text.count(" by ") == 1:
		search_text = search_text.replace(" by ", " ")
	if search_text.count(" - ") == 1:
		search_text = search_text.replace(" - ", " ")
	if search_text.count("-") == 1:
		search_text = search_text.replace("-", " ")

	if search_text.strip() == "":
		return None

	log("  Searching for " + search_text + " AND NOT Karaoke..", 3)
	try:
		spotify_login()
		results = spotify.search(search_text + " AND NOT Karaoke", limit=50, type='track')
	except Exception as err:
		log("Error searching for track", 1)
		log(str(err), 1)
		return None

	log("  Searching for track finished", 3)

	items = results['tracks']['items']

	choices = []
	track_hash = {}

	if len(items) > 0:
		for t in items:
			log("  Appending choice: " + t['artists'][0]['name'] + " " + t['name'], 3)
			choices.append(t['artists'][0]['name'] + " " + t['name'])
			track_hash[t['artists'][0]['name'] + " " + t['name']] = t
			log("  Appending choice: " + t['name'] + " " + t['artists'][0]['name'], 3)
			choices.append(t['name'] + " " + t['artists'][0]['name'])
			track_hash[t['name'] + " " + t['artists'][0]['name']] = t
			#choices.append(t['name'])
			#track_hash[t['name']] = t

		best_track = process.extractOne(search_text, choices)
		best_t = track_hash[best_track[0]]

		log("  Closest match: " + best_track[0] + " (" + str(best_track[1]) + ")" + " for comment [" + search_text + "]", 3)

		if fuzz.ratio(line, best_track[0]) > 50:
			log("  Returning track " + best_t['name'] + " for comment [" + line + "]", 2)
			return best_t
		else:
			log("  Fuzz ratio discarding '" + best_track[0] + "' with score: " + str(fuzz.ratio(line, best_track[0])), 3)

	return None

def find_tracks(submission):
	tracks = {}

	for track_comment in submission.comments:
		if isinstance(track_comment, praw.models.MoreComments):
			continue

		log("Parsing comment id " + str(track_comment.permalink), 3)

		for line in track_comment.body.split('\n'):
			if (not line):
				continue
			track = parse_comment(line)
			if track:
				if not track['uri'] in tracks:
					tracks[track['uri']] = track
					if track_comment.author:
						log("  Found track " + 
							track['uri'] + 
							" for author " + 
							track_comment.author.name, 2)

	return tracks

def split_dict_equally(input_dict, chunks=2):
	# prep with empty dicts
	return_list = [dict() for idx in xrange(chunks)]
	idx = 0
	for k,v in input_dict.iteritems():
		return_list[idx][k] = v
		if idx < chunks-1:  # indexes start at 0
			idx += 1
		else:
			idx = 0
	return return_list

def populate_playlist(playlist, tracks):

	try:
		spotify_login()

		# split it up into chunks of 50, because API won't handle that much
		for chunk in split_dict_equally(tracks, chunks=50):
			log("  Adding chunk of " + str(len(tracks)) + " tracks..", 1)
			spotify.user_playlist_add_tracks(spotipy_username, playlist['id'], chunk)
	except Exception as err:
		log("Error adding track(s)", 1)
		log(str(err), 1)
		print traceback.format_exc()

def create_playlist(title):

	try:
		playlist = spotify.user_playlist_create(spotipy_username, title)

		return playlist
	except Exception as err:
		log("Error creating playlist", 1)
		log(str(err), 1)

	return None

def comment_wants_playlist(body):
        lower_body = body.lower()

	# the magic keyword SpotifyBot always gets a request
	if "spotifybot" in lower_body:
		return True

	return False

def should_private_reply(submission, comment):
	# the jerks over in r/Music don't like bots to post
	if submission.subreddit.display_name.lower() == "music":
		return True

	# bots not allowed here, apparently
	if submission.subreddit.display_name.lower() == "askreddit":
		return True
	
	return False

def get_playlist_tracks(playlist_url):
	results = spotify.user_playlist_tracks(spotipy_username, playlist_url)

	tracks = results['items']
	while results['next']:
		results = spotify.next(results)
		tracks.extend(results['items'])

	return tracks

def update_existing_playlist(list_url, comment):
	if len(comment.body.split('\n')) > 3:
		# Skipping wall of text
		return False

	try:
		playlist = spotify.user_playlist(spotipy_username, list_url)
		if not playlist:
			log("  Could no longer find playlist", 1)
			return False

	except Exception as err:
		log("Error finding existing playlist", 1)
		log(str(err), 1)

		return False

	tracks = get_playlist_tracks(list_url)

	for line in comment.body.split('\n'):
		if not line:
			continue

		track = parse_comment(line)
		if track:
			log("  Updating existing playlist " + list_url, 1)
			found = False
			if len(tracks) > 0:
				for t in tracks:
					if track['uri'] == t['track']['uri']:
						found = True
						break
			if found == False:
				if comment.author:
					log("  Found new track, adding " + 
						track['uri'] + 
						" for author " + 
						comment.author.name, 2)

				try:
					spotify_login()
					spotify.user_playlist_add_tracks(
						spotipy_username, 
						playlist['id'], 
						{track['uri']:track})
				except Exception as err:
					log("Error adding track", 1)
					log(str(err), 1)
			else:
				log("  Track already in playlist, skipping", 2)

def create_new_playlist(reddit, submission, comment):

	tracks = find_tracks(submission)
	num_tracks = len(tracks)
	log("  Found " + str(num_tracks) + " tracks for new playlist", 2)

	# if we have less than 10 tracks, don't bother
	if num_tracks > 9:
		# add a new playlist
		new_playlist = create_playlist(submission.title)
		if new_playlist:
			playlist_url = new_playlist['external_urls']['spotify']
			playlist_name = new_playlist['name']

			log("  New playlist: " + playlist_url + " (" + playlist_name + ")", 1)

			populate_playlist(new_playlist, tracks)

			try:
				if should_private_reply(submission, comment):
					reddit.send_message(
						comment.author.name, 
						"Spotify Playlist", 
						msg_pm_created.format(
							comment=comment.permalink,
							submission=submission.url, 
							playlist=playlist_url))
				else:
					comment.reply(msg_created.format(playlist=playlist_url))
			except Exception as err:
				log("Unable to reply to reddit message: " + str(err), 1)

		append_comment_to_db(comment.id)
		append_submission_to_db(submission, new_playlist['external_urls']['spotify'])
		log("  comment and submission recorded in journal", 2)
	else:
		try:
			if should_private_reply(submission, comment):
				reddit.send_message(
					comment.author.name, 
					"Spotify Playlist", 
					msg_pm_no_tracks.format(
						comment=comment.permalink,
						submission=submission.url))
			else:
				comment.reply(msg_no_tracks)
		except Exception as err:
			log("Unable to reply to reddit messaeg: " + str(err), 1)

		append_comment_to_db(comment.id)
		log("  comment recorded in journal", 2)

def process_comment(reddit, comment):

	# calculate how far back in the queue we currently are
	timestamp = datetime.datetime.utcfromtimestamp(comment.created_utc)
	timestamp_now = datetime.datetime.utcnow()
	diff = (timestamp_now - timestamp)

	log("Processing comment id=" + comment.id + ", user=" + comment.author.name + ", time_ago=" + str(diff), 2)

	# fetch the submission/playlist and check if it's in our database already
	playlist_url = get_submission_playlist(comment.link_url)
	if playlist_url:
		# it's in our database, so see if this is another request, or another track
		log("  submission already recorded, checking comments", 2)
		if comment_wants_playlist(comment.body):
			log("  Sending existing playlist: " + playlist_url + " to " + comment.author.name, 1)
			submission = comment.submission
			if should_private_reply(submission, comment):
				reddit.send_message(
					comment.author.name, 
					"Spotify Playlist", 
					msg_pm_already_created.format(
						comment=comment.permalink,
						submission=submission.url, 
						playlist=playlist_url))
			else:
				comment.reply(msg_already_created.format(playlist=playlist_url))

			append_comment_to_db(comment.id)
		elif comment.is_root:
			# already processed this submission, but perhaps this is a new track to add
			try:
				update_existing_playlist(spotify, playlist_url, comment)
				append_comment_to_db(comment.id)
			except Exception as err:
				log("Error updating playlist", 1)
				log(str(err), 1)
				print traceback.format_exc()
		else:
			log("  Not a request for playlist, but also not a top-level comment", 2)

	else:
		# it's not in our database, so see if they are requesting a playlist
		if comment_wants_playlist(comment.body):
			submission = comment.submission

			log("\n----------- Create Playlist ------------------", 1)
			try:
				create_new_playlist(reddit, submission, comment)
			except Exception as err:
				log("Error creating new playlist", 1)
				log(str(err), 1)

def reddit_login():

	#reddit = praw.Reddit('Spotify Playlist B0t v1.0')
	#reddit.login(reddit_user, reddit_pw, disable_warning=True)
        reddit = praw.Reddit(user_agent='Spotify Playlist B0t',
                             client_id=reddit_client_id,
                             client_secret=reddit_client_secret,
                             username=reddit_user,
                             password=reddit_pw)

	return reddit

def spotify_login():
	global spotify
	global token
	global token_info
        global sp_oauth
        
	if not token or sp_oauth._is_token_expired(token_info):
		log("Spotify token expired, getting new one", 1)
		token_info = sp_oauth.get_cached_token()
		if not token_info:
			log("No token_info", 1)
			auth_url = sp_oauth.get_authorize_url()
			try:
				subprocess.call(['open', auth_url])
				print('Opening %s in your browser' % auth_url)
			except:
				print('Please navigate here: %s' % auth_url)
			print('')
			print('')
			try:
				response = raw_input('enter the URL you were redirected to: ')
			except NameError:
				response = input('Enter the URL you were directed to: ')

			print('')
			print('')

			code = sp_oauth.parse_response_code(response)
			token_info = sp_oauth.get_access_token(code)

			token = token_info['access_token']
		else:
			log("Refreshing access_token", 1)
			token_info = sp_oauth._refresh_access_token(token_info['refresh_token'])
			token = token_info['access_token']

		#token = util.prompt_for_user_token(
		#	spotipy_username,
		#	client_id=spotipy_client_id,
		#	client_secret=spotipy_client_secret,
		#	redirect_uri=spotipy_redirect_uri)

	if not token:
		log("Unable to login to spotify", 1)
		sys.exit()

	spotify = spotipy.Spotify(auth=token)
	log("Logged into spotify", 1)

def database_login():

	db = MySQLdb.connect(host="localhost", user=db_user, passwd=db_pw, db="spotifybot")

	return db

def test_search(search_text):
	global spotify

	spotify_login()

	track = parse_comment(spotify, search_text)

	if track:
		print("Best fit: " + track['name'] + " by " + track['artists'][0]['name'])
	else:
		print("No close match")

def test_submission(link_url):
	global spotify

	spotify_login()
	reddit = reddit_login()

	submission = praw.models.Submission(reddit, url=link_url)

	tracks = find_tracks(submission)

	print("Listing tracks..")
	for t in tracks:
		track = tracks[t]
		print(track['name'] + " by " + track['artists'][0]['name'])

def test_update_playlist(link_url):
	global spotify

	spotify_login()
	reddit = reddit_login()

	submission = praw.models.Submission(reddit, url=link_url)

	playlist_url = get_submission_playlist(link_url)
	if not playlist_url:
		log("Could no longer find playlist", 1)
		return False

	playlist = spotify.user_playlist(spotipy_username, playlist_url)

	tracks = find_tracks(submission)
	if not tracks:
		log("No tracks found to add", 1)
		return False

	try:
		populate_playlist(playlist, tracks)
	except Exception as e:
		log(str(e), 1)
		print traceback.format_exc()

	print("Playlist updated: " + playlist['external_urls']['spotify'])


def test_create_playlist(title, link_url):
	global spotify

	spotify_login()
	reddit = reddit_login()

	submission = praw.models.Submission(reddit, url=link_url)

	tracks = find_tracks(submission)

	new_playlist = create_playlist(title)
	if new_playlist:
		populate_playlist(new_playlist, tracks)

		print("New playlist created: " + new_playlist['external_urls']['spotify'])

def test_list_playlist(playlist_url):
	global spotify

	spotify_login()
	reddit = reddit_login()

	results = spotify.user_playlist_tracks(spotipy_username, playlist_url)
	tracks = results['items']
	while results['next']:
		results = spotify.next(results)
		tracks.extend(results['items'])

	print "Returned " + str(len(tracks)) + " tracks"

def main():
	global spotify

	# login to reddit for PRAW API
	reddit = reddit_login()
        subreddit = reddit.subreddit(subreddits)

	while True:
		# login to spotify, using their OAUTH2 API
		spotify_login()

		log("Looking for comments...", 1)
		try:
			for comment in subreddit.stream.comments():
				# skip comments we have already processed in our database
				if has_commented(comment.id):
					log("  already processed this comment, ignoring..", 2)
					continue

				# make sure user hasn't been deleted
				if not comment.author:
					log("  skipping comment without author..", 2)
					continue

				# make sure this comment isn't us!
				if comment.author.name == reddit_user:
					log("  skipping my own comment..", 2)
					continue

				try:
					# go ahead and attempt to process this comment
					process_comment(reddit, comment)

				except (ClientException, APIException, PRAWException) as e:
					log(str(e), 1)
					time.sleep(5)

				except Exception as e2:
					log(str(e2), 1)
					print traceback.format_exc()

		except ConnectionError as conn_err:
			log(str(conn_err), 1)
			time.sleep(1)

		except APIException as e:
			log(str(e), 1)
			time.sleep(1)

		except Exception as err2:
			log(str(err2), 1)
			print traceback.format_exc()
			time.sleep(5)

if __name__ == '__main__':

	# load playlists for quick retrieval
	load_submission_playlists()

	if len(sys.argv) > 1:
		log_level = 3

		if sys.argv[1] == "search":
			test_search(sys.argv[2])

		elif sys.argv[1] == "submission":
			test_submission(sys.argv[2])

		elif sys.argv[1] == "create_playlist":
			test_create_playlist(sys.argv[2], sys.argv[3])

		elif sys.argv[1] == "update_playlist":
			test_update_playlist(sys.argv[2])

		elif sys.argv[1] == "list_playlist":
			test_list_playlist(sys.argv[2])
	else:
		main()
