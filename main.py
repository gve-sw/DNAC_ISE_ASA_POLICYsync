'''
Copyright (c) 2020 Cisco and/or its affiliates.

This software is licensed to you under the terms of the Cisco Sample
Code License, Version 1.1 (the "License"). You may obtain a copy of the
License at

               https://developer.cisco.com/docs/licenses

All use of the material herein must be in accordance with the terms of
the License. All rights not expressly granted by the License are
reserved. Unless required by applicable law or agreed to separately in
writing, software distributed under the License is distributed on an "AS
IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
or implied.
'''

import logging
import socketserver
import requests
import ansible_runner
import time, yaml

# global vars
LOG_FILE = 'app.log'
HOST, PORT = "0.0.0.0", 514

# get credentials
config = yaml.safe_load(open("credentials.yml"))
ISE_username = config['ISE_username']
ISE_password = config['ISE_password']

# configure logging settings
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s', datefmt='', filename=LOG_FILE, filemode='a')

# Syslog Server class
class SyslogUDPHandler(socketserver.BaseRequestHandler):

	def handle(self):
		# get syslog message data
		global sgacl_content
		data = bytes.decode(self.request[0].strip())
		socket = self.request[1]
		search_data = str(data)

		# filter for syslog with new SGACL creation
		search_string1 = '52000 NOTICE Configuration-Changes: Added configuration'
		search_string2 = 'AdminInterface=ERS'
		search_string3 = 'mediaType=vnd.com.cisco.ise.trustsec.egressmatrixcell.1.0+xml'
		if search_string1 in search_data:
			if search_string2 in search_data:
				if search_string3 in search_data:

					print(search_data)

					# get ISE and SGACL information
					ISE_instance = self.client_address[0]
					find_bulkId = 'bulkId='
					s = search_data.partition(find_bulkId)[2]
					bulkId = s.split('\\', 1)[0]

					# get specs
					base_url = 'https://' + ISE_instance + ':9060/ers/config/'
					headers = {
						'Accept': 'application/json'
					}
					while True:
						get_egressmatrixcell = requests.get(base_url + 'egressmatrixcell/bulk/' + bulkId, headers=headers, auth=(ISE_username, ISE_password), verify=False)
						SearchResult = get_egressmatrixcell.json()
						egressmatrixcell_bulk = SearchResult['BulkStatus']['resourcesStatus'][0]
						egressmatrixcell_status = egressmatrixcell_bulk['status']
						if egressmatrixcell_status == 'SUCCESS':
							egressmatrixcell_ID = egressmatrixcell_bulk['id']
							break
						else:
							time.sleep(2)

					get_egressmatrixcell_content = requests.get(base_url + 'egressmatrixcell/' + egressmatrixcell_ID, headers=headers, auth=(ISE_username, ISE_password), verify = False)
					get_egressmatrixcell_content_result = get_egressmatrixcell_content.json()['EgressMatrixCell']
					egressmatrixcell_name = get_egressmatrixcell_content_result['name']

					logging.info("Egress Matrix Cell " + egressmatrixcell_name + " added on ISE instance " + ISE_instance) # add logging statement

					# get source and destination SGT name
					egressmatrixcell_sourceSGT_id = get_egressmatrixcell_content_result['sourceSgtId']
					get_sourceSGT = requests.get(base_url + 'sgt/' + egressmatrixcell_sourceSGT_id, headers=headers, auth=(ISE_username, ISE_password), verify = False)
					get_sourceSGT_result = get_sourceSGT.json()['Sgt']
					sourceSGT_name = get_sourceSGT_result['name']
					egressmatrixcell_destSGT_id = get_egressmatrixcell_content_result['destinationSgtId']
					get_destSGT = requests.get(base_url + 'sgt/' + egressmatrixcell_destSGT_id, headers=headers, auth=(ISE_username, ISE_password), verify = False)
					get_destSGT_result = get_destSGT.json()['Sgt']
					destSGT_name = get_destSGT_result['name']

					logging.info("Egress Matrix Cell " + egressmatrixcell_name + " configured with source SGT " + sourceSGT_name + " and destination SGT " + destSGT_name)  # add logging statement

					# get ACEs
					egressmatrixcell_sgacls_ids = get_egressmatrixcell_content_result['sgacls']
					egressmatrixcell_sgacls_statements = []
					for sgacl in egressmatrixcell_sgacls_ids:
						get_sgacl = requests.get(base_url + 'sgacl/' + sgacl, headers=headers, auth=(ISE_username, ISE_password), verify=False)
						get_sgacl_result = get_sgacl.json()['Sgacl']
						sgacl_name = get_sgacl_result['name']
						sgacl_content = get_sgacl_result['aclcontent']
						sgacl_entries = sgacl_content.split('\n')
						for entry in sgacl_entries:
							egressmatrixcell_sgacls_statements.append(entry)

					logging.info("Egress Matrix Cell " + egressmatrixcell_name + " configured with SGACL " + sgacl_name) # add logging statement

					acl_name_raw = egressmatrixcell_name + '_' + sgacl_name
					acl_name = acl_name_raw.replace(' ', '')

					# use SGACL information to prepare Ansible playbook
					acl_in_playbook = []
					for ACE in egressmatrixcell_sgacls_statements:
						ace = 'access-list ' + acl_name + ' extended ' + ACE + ' security-group name ' + sourceSGT_name + ' any security-group name ' + destSGT_name + ' any'
						acl_in_playbook.append(ace)

					with open('env/extravars') as f:
						doc = yaml.load(f, Loader=yaml.FullLoader)
					doc['acl_name'] = acl_name
					doc['acl_entries'] = acl_in_playbook
					with open('env/extravars', 'w') as f:
						yaml.safe_dump(doc, f)

					# run Ansible playbook to apply ACL to ASA
					r = ansible_runner.run(private_data_dir='.', playbook='asa_acl.yml')

					logging.info("Ansible playbook run on ASA with following results: " + str(r.stats)) # add logging statement


if __name__ == "__main__":
	# start the UDP Server
	try:
		server = socketserver.UDPServer((HOST,PORT), SyslogUDPHandler)
		server.serve_forever(poll_interval=0.5)
	except (IOError, SystemExit):
		raise
	except KeyboardInterrupt:
		print ("Crtl+C Pressed. Shutting down.")
