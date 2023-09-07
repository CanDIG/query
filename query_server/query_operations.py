from flask import request, Flask, session
import json
import requests
import secrets
import urllib

import config

PAGE_SIZE = 10000000

app = Flask(__name__)
app.config['SECRET_KEY'] = secrets.token_bytes(32)

def get_service_info():
    return {
        "id": "org.candig.query",
        "name": "CanDIG query service",
        "type": {
            "group": "org.candig",
            "artifact": "query",
            "version": "v0.1.0"
        },
        "description": "A query microservice for operating with HTSGet & Katsu",
        "organization": {
            "name": "CanDIG",
            "url": "https://www.distributedgenomics.ca"
        },
        "version": "0.1.0"
    }

# Grab a list of donors matching a given filter from the given URL
def get_donors_from_katsu(url, param_name, parameter_list):
    permissible_donors = set()
    for parameter in parameter_list:
        # TODO: Fix the page_size call here -- use a consume_all() query like in the frontend
        parameters = {
            param_name: parameter,
            'page_size': PAGE_SIZE
        }
        treatments = requests.get(f"{url}?{urllib.parse.urlencode(parameters)}", headers=request.headers)
        results = treatments.json()['results']
        permissible_donors |= set([result['submitter_donor_id'] for result in results])
    return permissible_donors

def add_or_increment(dict, key):
    if key in dict:
        dict[key] += 1
    else:
        dict[key] = 1

def get_summary_stats(donors, headers):
    # Perform (and cache) summary statistics
    diagnoses = requests.get(f"{config.KATSU_URL}/v2/authorized/primary_diagnoses/?page_size={PAGE_SIZE}",
        headers=headers).json()['results']
    # This search is inefficient O(m*n)
    # Should find a better way (Preferably SQL again)
    donor_ids = [donor['submitter_donor_id'] for donor in donors]
    donor_date_of_births = [donor['date_of_birth'] for donor in donors]
    age_at_diagnosis = {}
    for diagnosis in diagnoses:
        if diagnosis['submitter_donor_id'] in donor_ids:
            donor_idx = donor_ids.index(diagnosis['submitter_donor_id'])
            diag_date = diagnosis['date_of_diagnosis'].split('-')
            birth_date = donor_date_of_births[donor_idx].split('-')

            age = int(diag_date[0]) - int(birth_date[0])
            if int(diag_date[1]) >= int(birth_date[1]):
                age += 1
            age = age // 10 * 10
            if age < 20:
                add_or_increment(age_at_diagnosis, '0-19 Years')
            elif age > 79:
                add_or_increment(age_at_diagnosis, '80+ Years')
            else:
                add_or_increment(age_at_diagnosis, f'{age}-{age+9} Years')

    # Treatment types
    # http://candig.docker.internal:8008/v2/authorized/treatments/
    treatments = requests.get(f"{config.KATSU_URL}/v2/authorized/treatments/?page_size={PAGE_SIZE}",
        headers=headers).json()['results']
    treatment_type_count = {}
    for treatment in treatments:
        # This search is inefficient O(m*n)
        if treatment['submitter_donor_id'] in donor_ids:
            for treatment_type in treatment['treatment_type']:
                add_or_increment(treatment_type_count, treatment_type)

    # Cancer types
    cancer_type_count = {}
    patients_per_cohort = {}
    for donor in donors:
        for cancer_type in donor['primary_site']:
            if cancer_type in cancer_type_count:
                cancer_type_count[cancer_type] += 1
            else:
                cancer_type_count[cancer_type] = 1
        program_id = donor['program_id']
        if program_id in patients_per_cohort:
            patients_per_cohort[program_id] += 1
        else:
            patients_per_cohort[program_id] = 1
    
    return {
        'age_at_diagnosis': age_at_diagnosis,
        'treatment_type_count': treatment_type_count,
        'cancer_type_count': cancer_type_count,
        'patients_per_cohort': patients_per_cohort
    }

def query_htsget_gene(gene):
    payload = {
        'query': {
            'requestParameters': {
                'gene_id': gene
            }
        },
        'meta': {
            'apiVersion': 'v2'
        }
    }

    return requests.post(
        f"{config.HTSGET_URL}/beacon/v2/g_variants/",
        headers=request.headers,
        json=payload).json()

def query_htsget_pos(assembly, chrom, start=0, end=10000000):
    payload = {
        'query': {
            'requestParameters': {
                'assemblyId': assembly,
                'referenceName': chrom,
                'start': start,
                'end': end
            }
        },
        'meta': {
            'apiVersion': 'v2'
        }
    }

    return requests.post(
        f"{config.HTSGET_URL}/beacon/v2/g_variants/",
        headers=request.headers,
        json=payload).json()

@app.route('/query')
def query(treatment="", primary_site="", chemotherapy="", immunotherapy="", hormone_therapy="", chrom="", gene="", page=0, page_size=10, session_id=""):
    # NB: We're still doing table joins here, which is probably not where we want to do them
    # We're grabbing (and storing in memory) all the donor data in Katsu with the below request

    # Query the appropriate Katsu endpoint
    params = { 'page_size': PAGE_SIZE }
    url = f"{config.KATSU_URL}/v2/authorized/donors/"
    if primary_site != "":
        params['primary_site'] = ",".join(primary_site)
    r = requests.get(f"{url}?{urllib.parse.urlencode(params)}",
        # Reuse their bearer token
        headers=request.headers)
    donors = r.json()['results']

    # Will need to look into how to go about this -- ideally we implement this into the SQL in Katsu's side
    filters = [
        (treatment, f"{config.KATSU_URL}/v2/authorized/treatments/", 'treatment_type'),
        (chemotherapy, f"{config.KATSU_URL}/v2/authorized/chemotherapies/", 'drug_name'),
        (immunotherapy, f"{config.KATSU_URL}/v2/authorized/immunotherapies/", 'drug_name'),
        (hormone_therapy, f"{config.KATSU_URL}/v2/authorized/hormone_therapies/", 'drug_name')
    ]
    for (this_filter, url, param_name) in filters:
        if this_filter != "":
            permissible_donors = get_donors_from_katsu(
                url,
                param_name,
                this_filter
            )
            donors = [donor for donor in donors if donor['submitter_donor_id'] in permissible_donors]
    summary_stats = get_summary_stats(donors, request.headers)

    # Now we combine this with HTSGet, if any
    if gene or chrom:
        if gene:
            htsget = query_htsget_gene(gene)
        else:
            search = re.search('(chr[XY0-9]{2}):(\d+)-(\d+)', 'chrom')
            htsget = query_htsget_pos('hg37', search.group(0), search.group(1), search.group(2))
        results = htsget['results']['beaconHandovers']
    
    # TODO: Cache the above list of donor IDs and summary statistics
    ret_donors = [donor['submitter_donor_id'] for donor in donors[(page*page_size):((page+1)*page_size)]]
    params = {
        'page_size': PAGE_SIZE,
        'donors': ','.join(ret_donors)
    }
    r = requests.get(f"{config.KATSU_URL}/v2/authorized/donor_with_clinical_data/?{urllib.parse.urlencode(params)}",
        headers=request.headers)
    full_data = r.json()
    full_data['count'] = len(donors)
    full_data['summary'] = summary_stats
    full_data['next'] = None
    full_data['prev'] = None

    # Add prev and next parameters to the repsonse, appending a session ID.
    # Essentially we want to go session ID -> list of donors
    # and then paginate the list of donors, calling donors_with_clinical_data on each before returning
    print(json.dumps(full_data))
    return full_data, 200
