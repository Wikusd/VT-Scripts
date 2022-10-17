#!/usr/bin/python
import json
from operator import contains
from cvpysdk.commcell import Commcell
from cvpysdk.constants import AdvancedJobDetailType
from cvpysdk.instance import Instances
from pprint import pprint
from datasize import DataSize
import os
import platform
import re
import requests
from datetime import datetime

if platform.system() == "Windows":
  os.system('cls')
else:
  os.system('clear')
 
excludedServers = []
backupCheckList = ['za-prk-core-bk01']
excludeFromHashChecks = ['tradedesk_prod']
maxShrink = 5 # in percentage, if shrinks more than this its a fail.


class sDict(dict):
  def find(self, element):
    paths = element.split(".")
    data = self
    for i in range(0,len(paths)):
      keys = {str(key): key for key in data.keys()}
      if paths[i] in keys:
        data = data[keys[paths[i]]]
      else:
        raise Exception(f"No path found for {i} {paths[i]} [{data}]")

    return data


def CleanSize(s):
  return re.sub(r'[a-z]+', '', s, flags=re.IGNORECASE)

webconsole_hostname = "access.storvault.co.za"
commcell_username = "ronaldd@za.velocitytrade.com"
commcell_password = "s34MYBr4InS2201/!"

def getReport(commcell, clientId, cacheId='', offset=0, limit=50, maxClients=1, numEntries=100, lastBackupTime='-P1D P0D'):
  reportUrl = "https://access.storvault.co.za/webconsole/api/cr/reportsplusengine/datasets/af66e5d1-6354-44d0-9602-3d71387a06d9:7c7cd38b-4e70-440a-857b-d08f987bdeb7/data/"
  params = {
    'offset':0,
    'parameter.ClientId[]':clientId, #927,
    'parameter.Options[]':1,
    'parameter.FileSizeFilter':-1,
    'parameter.DateModifiedFilter':-1,
    'parameter.NumEntries':numEntries,
    'parameter.MaxClients':maxClients,
    'parameter.LastBackupTime':lastBackupTime, # -P1D P0D, -P1M P0D
    'limit':limit
  }

  if cacheId != '':
    params["cacheId"] = cacheId
    params["offset"] = offset

  clientIndex = -1
  pathIndex = -1
  sizeIndex = -1
  modificationTimeIndex = -1
  backupTimeIndex = -1

  r=requests.get(reportUrl, headers=commcell._headers, params=params)
  j=json.loads(r.content)

  cacheId = j["cacheId"]
  columns = j["columns"]
  data = j["records"]

  i = 0
  for item in columns:
    if item["name"] == 'Client':
      clientIndex = i

    if item["name"] == 'Path':
      pathIndex = i

    if item["name"] == 'Size':
      sizeIndex = i

    if item["name"] == 'ModificationTime':
      modificationTimeIndex = i

    if item["name"] == 'BackupTime':
      backupTimeIndex = i

    i += 1

  def mapper(data):
    pthParts = data[pathIndex].split('/')
    pth = pthParts[-2]
    folder = pth.split('-')
    bckTime = data[backupTimeIndex]
    modTime = data[modificationTimeIndex]
    return {
      "Client" : data[clientIndex],
      "Path" : data[pathIndex],
      "Service" : folder[0],
      "File" : pthParts[-1],
      "Hash" : folder[1],
      "Size" : data[sizeIndex],
      "SizeReadable" : '{:.2a}'.format(DataSize(data[sizeIndex])).replace("i", ""),
      "ModificationTimestamp" : modTime,
      "ModificationTime" : datetime.fromtimestamp(modTime).strftime('%Y-%m-%d %H:%M:%S'),
      "BackupTimestamp" : bckTime,
      "BackupTime" : datetime.fromtimestamp(bckTime).strftime('%Y-%m-%d %H:%M:%S')
    }

  mapped = list(map(mapper, data))   
  # print(f'Count {cacheId} : {len(mapped)}')
  #pprint(mapped)

  fileList = []

  for item in mapped:
    if item["Service"] not in fileList:
      fileList.append(item["Service"])

  # print(fileList)

  return mapped, fileList, cacheId


def sortReport(mapped):
  return sorted(mapped, key=lambda x: x["ModificationTimestamp"])

def getReportItems(report, fieldName, fieldValue):
  matching = [s for s in report if fieldValue in s[fieldName]]
  return matching

def getReportItems2(report, fieldName1, fieldValue1, fieldName2, fieldValue2):
  matching = [s for s in report if fieldValue1 in s[fieldName1] and fieldValue2 in s[fieldName2]]
  return matching

def checkReports(reports, allServices, doHashCheck):
  def getHasShrunk(lastVal, val):
    return val <= (lastVal - (lastVal / 100 * maxShrink))

  #pprint(reports)

  ok = []
  fail = []
  avgCount = {}
  avgValue = {}

  for service in allServices:
    avgCount[service] = 0
    avgValue[service] = 0

  for index in range(0, len(reports)):
    item = reports[index]

    thisService = item["Service"]
    size = item["Size"]
    hash = item["Hash"]
    
    avgCount[thisService] += 1
    avgValue[thisService] += size

    if avgCount[thisService] == 1:
      continue

    lastSize = reports[index - 1]["Size"]
    lastHash = reports[index - 1]["Hash"]

    hasShrunk = getHasShrunk(lastSize, size)
    hashMatch = lastHash == hash

    if hasShrunk or (doHashCheck and hashMatch):
      item["HasShrunk"] = f"{hasShrunk}"
      item["HashMatch"] = f"{hashMatch}"
      fail.append(item)
    else:
      item["HasShrunk"] = f"False"
      item["HashMatch"] = f"False"
      ok.append(item)

  lastBackups = []

  # pprint(avgValue)
  # pprint(avgCount)

  for service in allServices:
    if avgCount[service] == 0:
      continue

    avgSize = avgValue[service] / avgCount[service]
    serviceData = getReportItems(reports, "Service", service)[-1]
    reportSmallerThanAvg = getHasShrunk(avgSize, serviceData["Size"])
    if not reportSmallerThanAvg:
      lastBackups.append(serviceData)

  return ok, fail, lastBackups


def getFullReport(commcell, clientId):
  allData = []
  allServices = []
  allFiles = {}

  def appendServices(newServices):
    for item in newServices:
      if item not in allServices:
        allServices.append(item)

  def appendFile(data):
    if data["Service"] not in allFiles.keys():
      allFiles[data["Service"]] = []

    if data["File"] not in allFiles[data["Service"]]:
      allFiles[data["Service"]].append(data["File"])

  lastBackupTime='-P2D P0D'
  offset = 0
  limit = 50

  mapped, services, cacheId = getReport(commcell, clientId, offset=offset, lastBackupTime=lastBackupTime)
  allData.extend(mapped)
  appendServices(services)

  while len(mapped) > 0:
    offset += limit
    mapped, services, cacheId = getReport(commcell, clientId, cacheId=cacheId, offset=offset, lastBackupTime=lastBackupTime)
    allData.extend(mapped)
    appendServices(services)

  list(map(appendFile, allData))

  return sortReport(allData), allServices, allFiles

commcell = Commcell(webconsole_hostname, commcell_username, commcell_password)

clients = commcell.clients
clientList = clients.all_clients

opts = {"job_summary" : "full"}
selected = list(clientList.items())[0][0]
thisClient = clients.get(selected)

for thisClient in clientList:
  if thisClient in excludedServers:
    continue

  thisClientInfo = clients.get(thisClient)
  print(f"{thisClient}, Id: {thisClientInfo.client_id}")

  jobs = sDict(commcell.job_controller.finished_jobs(thisClient))
  jobIds = jobs.keys()

  if len(jobIds) == 0:
    print(f"  NO JOBS FOUND!?")
    print()
  else:
    for jobId in jobIds:
        complete = jobs.find(f"{jobId}.percent_complete")
        status = jobs.find(f"{jobId}.status")

        details = sDict(commcell.job_controller.get(jobId).details)
        events = commcell.job_controller.get(jobId).get_events()

        sizeInBytes = details.find("jobDetail.detailInfo.unCompressedBytes")
        unCompressedBytes = DataSize(sizeInBytes)
        dataSize = '{:.2a}'.format(unCompressedBytes).replace("i", "")
        clientId = details.find("jobDetail.generalInfo.subclient.clientId")

        print(f"  JobId: {jobId} {complete}% {status}, {dataSize}")
        print()

        # pprint(events)
        # print("Details")
        # pprint(details)

  if thisClient in backupCheckList:
    mapped, services, allFiles = getFullReport(commcell, thisClientInfo.client_id)

    for service in services:
      print(f"  {service} File Check Details:")
      doHashCheck = service not in excludeFromHashChecks

      for serviceFile in allFiles[service]:
        serviceReports = getReportItems2(mapped, "Service", service, "File", serviceFile)
        ok, fail, lastBackup = checkReports(serviceReports, services, doHashCheck)

        hashFailed = getReportItems(fail, "HashMatch", "True")
        sizeFailed = getReportItems(fail, "HasShrunk", "True")
        
        hasLastBackup = len(lastBackup) > 0        
        if hasLastBackup:
          lastBackup = lastBackup[0]
          lastBackupHashMatched = lastBackup["HashMatch"]
          lastBackupSizeFailed = lastBackup["HasShrunk"]
          print(f"    {serviceFile}, last backup has PASSED with Hash Matched {lastBackupHashMatched}, Data size Shrunk {lastBackupSizeFailed}")
        else:
          print(f"    {serviceFile}, last backup has FAILED all checks see below.")

        # -1 due to the fact that the oldest backup gets used to decide if the rest are ok
        print(f"      File checks {len(serviceReports) - 1}, Passed: {len(ok)}, Failed: {len(fail)} of which Hash Failed: {len(hashFailed)}, Size Failed: {len(sizeFailed)}")
        print()

      # if service == "pt3_prod":
      #   pprint(lastBackup)
      
      # pprint(serviceReports)
      # pprint(sizeFailed)
      # pprint(lastBackup)
      #exit(1)

    print()
    # exit(1)

