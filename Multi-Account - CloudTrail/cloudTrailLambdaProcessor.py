import boto3, gzip, json, os, sys

s3Client = boto3.client('s3')
firehoseDeliverySreamName = os.environ['firehoseDeliverySreamName']
firehoseClient = boto3.client('firehose', region_name=os.environ['AWS_REGION'])

def retrieveS3Object(record):

	try:
		# Set bucket and key to retrieve
		record = json.loads(record['body'])
		bucket = record['Records'][0]['s3']['bucket']['name']
		key = record['Records'][0]['s3']['object']['key']

	except:
		return("SQS message did not contain S3 file information.  Record: " + str(record))

	if (("CloudTrail" not in key or "json.gz" not in key) or ("CloudTrail-Digest" in key)):
		return("Skipping file s3://" + bucket + "" + key)

	try:
		# Download CloudTrail file from S3
		s3Client.download_file(bucket, key, "/tmp/" + key.split("/")[-1] + ".gz")
	except:
		return("Unable to download file s3://" + bucket + "/" + key)

	return("Downloaded CloudTrail file s3://" + bucket + "/" + key)


def processCloudTrailFile(record):

	# Set bucket and key to retrieve
	record = json.loads(record['body'])
	bucket = record['Records'][0]['s3']['bucket']['name']
	key = record['Records'][0]['s3']['object']['key']

	try:
		with gzip.open("/tmp/" + key.split("/")[-1] + ".gz", "rb") as f:
			data = f.read().decode("ascii")

	except:
		return("Unable to decode file s3://" + bucket + "/" + key)

	
	# Parse JSON data
	try:
		cloudTrailRecords = json.loads(data)["Records"]
	except:
		return("Unable to CloudTrail records from s3://" + bucket + "/" + key)


	# Send data to Firehose
	try:

		recordBatch = []

		for cloudTrailRecord in cloudTrailRecords:
			# Add record to recordbatch
			recordBatch.append({"Data": json.dumps(cloudTrailRecord) + "\r\n"})

			# If there are more than 250 records or 2MB in the sending queue, send the event to Splunk and clear the queue
			if (len(recordBatch) > 250 or (sys.getsizeof(recordBatch) > 2000000 )):
				firehoseClient.put_record_batch(DeliveryStreamName=firehoseDeliverySreamName, Records=recordBatch)
				recordBatch.clear()

		# Send any remaining records to Splunk
		if (len(recordBatch) > 0):
			firehoseClient.put_record_batch(DeliveryStreamName=firehoseDeliverySreamName, Records=recordBatch)
			recordBatch.clear()

	except:
		return("Unable to send record to Firehose s3://" + bucket + "/" + key)


	return("Processed CloudTrail records from s3://" + bucket + "/" + key)


def handler(event, context):

	for record in event['Records']:

		# Parse SQS message and download file from S3
		retrievalResult = retrieveS3Object(record)
		print(retrievalResult)		

		# Process file, only if it was successfully downloaded
		if ("Downloaded CloudTrail" in retrievalResult):
			processResult = processCloudTrailFile(record)
			print(processResult)