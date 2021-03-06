import boto3, gzip, json, os, sys, shutil, re, dateutil.parser, time, csv

# AWS-related setup
s3Client = boto3.client('s3')
firehoseDeliverySreamName = os.environ['firehoseDeliverySreamName']
firehoseClient = boto3.client('firehose', region_name=os.environ['AWS_REGION'])
recordBatch = []

# Splunk-related setup
SPLUNK_INDEX = os.environ['SPLUNK_INDEX']
SPLUNK_TIME_PREFIX = os.environ['SPLUNK_TIME_PREFIX']
SPLUNK_EVENT_DELIMITER = os.environ['SPLUNK_EVENT_DELIMITER']
SPLUNK_TIME_DELINEATED_FIELD = os.environ['SPLUNK_TIME_DELINEATED_FIELD']
SPLUNK_TIME_FORMAT = os.environ['SPLUNK_TIME_FORMAT']
SPLUNK_SOURCETYPE = os.environ['SPLUNK_SOURCETYPE']
SPLUNK_SOURCE = os.environ['SPLUNK_SOURCE']
SPLUNK_HOST = os.environ['SPLUNK_HOST']
SPLUNK_JSON_FORMAT = os.environ['SPLUNK_JSON_FORMAT']
SPLUNK_IGNORE_FIRST_LINE = os.environ['SPLUNK_IGNORE_FIRST_LINE']
SPLUNK_CSV_TO_JSON = os.environ['SPLUNK_CSV_TO_JSON']
SPLUNK_REMOVE_EMPTY_CSV_TO_JSON_FIELDS = os.environ['SPLUNK_REMOVE_EMPTY_CSV_TO_JSON_FIELDS']

# Lambda things
validFileTypes = ["gz", "gzip", "json", "csv", "log"]
unsupportedFileTypes = ["CloudTrail-Digest", "billing-report-Manifest"]
delimiterMapping = {"space": " ", "tab": "	", "comma": ",", "semicolon": ";"}

# Create delimiter for delimiting events
def createDdelimiter():

	if (SPLUNK_EVENT_DELIMITER in delimiterMapping.keys()):
		return delimiterMapping[SPLUNK_EVENT_DELIMITER]
	else:
		return SPLUNK_EVENT_DELIMITER


# Parse SQS message for bucket information
def retrieveObjectInfo(record):
	
	# Try to parse the record for file information
	try:
		record = json.loads(record['body'])
		bucket = record['Records'][0]['s3']['bucket']['name']
		key = record['Records'][0]['s3']['object']['key']

		# Construct and return the result
		result = {}
		result["bucket"] = bucket
		result["key"] = key
		return result

	# Return an error if the record doesn't have a valid file defined in it
	except:
		return("SQS message did not contain S3 file information.  Record: " + str(record))


# Check to see if the file is a valid file type
def validateFileType(key):

	# Check for invalid file types
	for unsupportedFileType in unsupportedFileTypes:
		if (unsupportedFileType in key):
			return("Unsupported file type.")

	# Define file extension
	extension = key.split(".")[-1]

	# Check for valid file types
	for validFileType in validFileTypes:
		if (extension in validFileType):
			return("Valid file type.")

	return("Unsupported file type.")


# Retrieve the S3 object, and return the new path
def downloadS3Object(bucket, key):

	try:
		# Define the path for the file
		path = "/tmp/" + key.split("/")[-1]

		# Download the file from the S3 bucket
		s3Client.download_file(bucket, key, path)

		# Return the new file path
		return(path)

	except:
		return("Unable to download file s3://" + bucket + "/" + key)


# Uncompress the file if it needs to be uncompressed, then return the path and the new file extension
def uncompressFile(path):

	# Set file extension and new file path (if it gets uncompressed)
	extension = path.split(".")[-1]
	uncompressedFilePath = path[0:(-1*(len(extension)) - 1)]

	try:
		# If gzip file...
		if (extension == "gz" or extension == "gzip"):

			# Uncompress file
			with gzip.open(path, 'rb') as f_in:
				with open(uncompressedFilePath, 'wb') as f_out:
					shutil.copyfileobj(f_in, f_out)

			# Remove the uncompressed file
			os.remove(path)

			return(uncompressedFilePath)

	except:
		return("Unable to uncompress file")

	return(path)


# Split events into a list. Additional file extensions should be added here.
def eventBreak(events, extension):

	if (extension == "csv" or extension == "log"):
		splitEvents = events.split("\n")

		# Remove empty last line if it exists
		if (len(splitEvents[-1]) == 0):
			splitEvents = splitEvents[:-1]

		if (SPLUNK_IGNORE_FIRST_LINE == "true"):
			splitEvents = splitEvents[1:]
		
		events = ""

		return splitEvents

	elif (extension == "json"):
		if (SPLUNK_JSON_FORMAT == "eventsInRecords"):
			splitEvents = json.loads(events)["Records"]
			events = ""

			return splitEvents

		elif (SPLUNK_JSON_FORMAT == "NDJSON"):
			splitEvents = events.split("\n")
			events = ""
			
			if (len(splitEvents[-1]) == 0):
				splitEvents = splitEvents[:-1]

			return splitEvents

	else: 
		return("File type invalid")


# Clean up first line 
def cleanFirstLine(splitEvents):

	# If the sourcetype is aws:billing:cur, remove everything before the "/" in the CSV header
	if (SPLUNK_SOURCETYPE == "aws:billing:cur"):
		
		header = splitEvents[0]
		
		newHeader = ""
		
		for splitHeader in header.split(","):
			newHeader += str(splitHeader.split("/")[1]) + ","
		
		splitEvents[0] = newHeader[:-1]

	return(splitEvents)


# Handle CSV to JSON conversion, and optionally remove null fields
def csvToJSON(splitEvents):

	newEvents = []

	# Change CSVs with headers to JSON format
	csvSplit = csv.DictReader(splitEvents)
	for csvRow in csvSplit: 
		newEvents.append(csvRow)

	# Remove JSON fields with null or no value
	if (SPLUNK_REMOVE_EMPTY_CSV_TO_JSON_FIELDS == "true"):

		newEventsWithoutEmptyValues = []
		for newEvent in newEvents:
			newEventWithoutEmptyValues = {}
			for newEventKey in newEvent.keys():
				if (len(newEvent[newEventKey]) > 0):
					newEventWithoutEmptyValues[newEventKey] = newEvent[newEventKey]
			newEventsWithoutEmptyValues.append(newEventWithoutEmptyValues)

		newEvents.clear()
		return newEventsWithoutEmptyValues

	return newEvents


# Set timestamp on event
def getTimestamp(event, delimiter):

	try:
		# For ISO8601 (%Y-%m-%dT%H-%M-%S.%fZ)
		if (SPLUNK_TIME_FORMAT == "prefix-ISO8601"):

			if (len(SPLUNK_TIME_PREFIX) > 0):
				regexGroupIndex = 2
			else: 
				regexGroupIndex = 0

			iso8601Timestamp = re.search("" + SPLUNK_TIME_PREFIX + "(.{1,5})?(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(.\d{0,10})?Z)", str(event)).group(regexGroupIndex) #fix eventTime
			return(dateutil.parser.parse(iso8601Timestamp).timestamp())
		# For field-delimited epoch time
		elif (SPLUNK_TIME_FORMAT == "delineated-epoch"):
			epochTime = float(evenIMITED(delimiter)[int(SPLUNK_TIME_DELINEATED_FIELD)])
			return(epochTime)
		# For delineated ISO8601 (%Y-%m-%dT%H-%M-%S.%fZ)
		elif (SPLUNK_TIME_FORMAT == "delineated-ISO8601"):
			iso8601Timestamp = event.split(delimiter)[int(SPLUNK_TIME_DELINEATED_FIELD)]
			return(dateutil.parser.parse(iso8601Timestamp).timestamp())
	except:
		# If not standard, set to current time
		print("Unable to extract timestamp.  Falling back to current time.")
		return(time.time())

	return(time.time())


# Buffer and send events to Firehose
def sendEventsToFirehose(event, final):

	# Add current event ot recordBatch
	if (len(event) > 0): # This will be 0 if it's a final call to clear the buffer
		recordBatch.append({"Data": event})

	try:

		# If there are more than 200 records or 2MB in the sending queue, send the event to Splunk and clear the queue
		if (len(recordBatch) > 200 or (sys.getsizeof(recordBatch) > 2000000 )):
			firehoseClient.put_record_batch(DeliveryStreamName=firehoseDeliverySreamName, Records=recordBatch)
			recordBatch.clear()
		elif (final == True):
			firehoseClient.put_record_batch(DeliveryStreamName=firehoseDeliverySreamName, Records=recordBatch)
			recordBatch.clear()
	except:
		return("Unable to send file to Firehose")

	return("Sent to Firehose")


# Default Lambda handler
def handler(event, context):

	# Create delineated field break
	delimiter = createDdelimiter()

	# Loop through each SQS message
	for message in event['Records']:

		# Retrieve bucket name and key from SQS message
		objectInfo = retrieveObjectInfo(message)

		# If a string was returned instead of a dictionary, print the error and stop this loop
		if (isinstance(objectInfo, str)):
			print(objectInfo)
			continue

		# Validate file types
		validateFileTypeResult = validateFileType(objectInfo["key"])
		if ("Unsupported file type." in validateFileTypeResult):
			print("Unsupported file type: s3://" + objectInfo["bucket"] + "/" + objectInfo["key"])
			continue
		
		# Retrieve the S3 object and uncompress it
		downloadResult = downloadS3Object(objectInfo["bucket"], objectInfo["key"])
		
		# If the file was unable to be downloaded, print the error and stop this loop
		if ("Unable to download" in downloadResult):
			print(downloadResult)
			continue

		# Send file info to be uncompressed
		uncompressResult = uncompressFile(downloadResult)

		# If the file was unable to be compressed, print the error and stop this loop
		if ("Unable to uncompress file" in uncompressResult):
			print("Unable to uncompress file s3://" + objectInfo["bucket"] + "/" + objectInfo["key"])
			continue

		# Try to read the file contents into memory
		try:
			with open(uncompressResult, 'r') as f:
				events = f.read()
		except:
			print("Unable to read file contents into memory")
			continue

		# Set extension 
		extension = uncompressResult.split(".")[-1]

		# Split events
		splitEvents = eventBreak(events, extension)

		# Clean up first line of events
		if (SPLUNK_SOURCETYPE == "aws:billing:cur"):
			splitEvents = cleanFirstLine(splitEvents)

		# If a string was returned instead of a list, print the error and stop this loop
		if (isinstance(splitEvents, str)):
			print("File type unsupported s3://" + objectInfo["bucket"] + "/" + objectInfo["key"])
			continue

		# Transform CSV to JSON
		if (SPLUNK_CSV_TO_JSON == "true"):
			splitEvents = csvToJSON(splitEvents)

		# Loop through split events
		for splitEvent in splitEvents:

			# Get timestamp
			timestamp = getTimestamp(splitEvent, delimiter)

			# Construct event to send to Splunk
			splunkEvent = '{ "time": ' +  str(timestamp) + ', "host": "' + SPLUNK_HOST + '", "source": "' + SPLUNK_SOURCE + '", "sourcetype": "' + SPLUNK_SOURCETYPE + '", "index": "' + SPLUNK_INDEX + '", "event":  ' + json.dumps(splitEvent) + ' }'

			# Buffer and send the events to Firehose
			result = sendEventsToFirehose(str(splunkEvent), False)

			# Error logging
			if (result == "Unable to send to Firehose"):
				print("Unable to send to Firehose: " + firehoseDeliverySreamName)

		# Send the remaining events to Firehose, effectively clearing the buffered events in recordBatch
		sendEventsToFirehose("", True)

		# Delete the file to clear up space in /tmp to make room for the next one
		os.remove(uncompressResult)

		# Logging
		print("Processed file s3://" + objectInfo["bucket"] + "/" + objectInfo["key"])

