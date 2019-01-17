# Copyright 2017-present Open Networking Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

*** Settings ***
Documentation     Test various E2E conditions for seba-in-a-box
Suite Setup       Setup
Suite Teardown    Teardown
Library           Collections
Library           String
Library           OperatingSystem
Library           XML
Library           RequestsLibrary
Library           ../../Framework/utils/utils.py
Resource          ../../Framework/utils/utils.robot
Library           ../../Framework/restApi.py
Resource          ../../Framework/Subscriber.robot
Resource          ../../Framework/ATTWorkFlowDriver.robot
Resource          ../../Framework/Kubernetes.robot
Resource          ../../Framework/ONU.robot
Resource          ../../Framework/DHCP.robot
Variables         ../../Properties/RestApiProperties.py

*** Variables ***
${export_kube_config}      export KUBECONFIG=/home/%{USER}/.kube/config
${kube_node_ip}            localhost
${dst_host_ip}             172.18.0.10
${local_user}              %{USER}
${local_pass}              %{USER}

*** Test Cases ***
ONUs Discovered
    [Documentation]    Validates All ONU Devices are discovered and retrieve SNs
    [Tags]    onudiscovery
    Wait Until Keyword Succeeds    120s    5s    Validate Number of ONU Devices    16

Validate ONU States
    [Documentation]    Validates All ONU Device states are "enabled" and "active"
    [Tags]    onustates
    : FOR    ${onu}    IN    @{serial_numbers}
    \    Validate ONU States    ACTIVE    ENABLED    ${onu}

Validate ATT WF Driver SIs
    [Documentation]    Validates all service instances per onu devices become "approved" and "dhcpdiscovered"
    [Tags]    serviceinstances
    : FOR    ${onu}    IN    @{serial_numbers}
    \    Wait Until Keyword Succeeds    120s    2s    Validate ATT Workflow Driver SI    ENABLED    APPROVED    ${onu}
    \    Wait Until Keyword Succeeds    120s    2s    Validate ATT Workflow Driver SI DHCP State    DHCPDISCOVER    ${onu}

*** Keywords ***
Setup
    ${auth} =    Create List    ${XOS_USER}    ${XOS_PASSWD}
    ${HEADERS}    Create Dictionary    Content-Type=application/json
    Create Session    ${server_ip}    http://${server_ip}:${server_port}    auth=${AUTH}    headers=${HEADERS}

Teardown
    Delete All Sessions

Validate Number of ONU Devices
    [Arguments]    ${expected_onus}
    ${resp}=    CORD Get    ${ONU_DEVICE}
    ${jsondata}=    To Json    ${resp.content}
    Log    ${jsondata}
    ${length}=    Get Length    ${jsondata['items']}
    @{serial_numbers}=    Create List
    : FOR    ${INDEX}    IN RANGE    0    ${length}
    \    ${value}=    Get From List    ${jsondata['items']}    ${INDEX}
    \    ${sn}=    Get From Dictionary    ${value}    serial_number
    \    ${contains}=    Evaluate    "BBSM" in """${sn}"""
    \    Run Keyword if    '${contains}' == 'True'    Append To List    ${serial_numbers}    ${sn}
    Set Suite Variable    ${serial_numbers}
    ${length_of_bbsim_onus}=    Get Length    ${serial_numbers}
    Should Be Equal as Integers    ${length_of_bbsim_onus}    ${expected_onus}