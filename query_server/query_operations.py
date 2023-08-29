from flask import request, Flask, session
import requests
import secrets
import config

app = Flask(__name__)
app.config['SECRET_KEY'] = secrets.token_bytes(32)

# Grab a list of donors matching a given filter from the given URL
def get_donors_from_katsu(url, parameter_list):
    permissible_donors = set()
    for parameter in parameter_list:
        # TODO: Fix the page_size call here -- use a consume_all() query like in the frontend
        treatments = requests.get((url % parameter) + "&page_size=10000000", headers=request.headers)
        results = treatments.json()['results']
        permissible_donors |= set([result['submitter_donor_id'] for result in results])
    return permissible_donors

@app.route('/query')
def query(treatment="", primary_site="", chemotherapy="", immunotherapy="", hormone_therapy="", chrom="", gene="", page=0, page_size=10, session_id=""):
    # NB: We're still doing table joins here, which is probably not where we want to do them
    # We're grabbing (and storing in memory) all the data in Katsu with the below request

    # Query the appropriate Katsu endpoint
    url = f"{config.KATSU_URL}/v2/authorized/donors/?page_size=10000000"
    if primary_site != "":
        url += "&primary_site=" + ",".join(primary_site)
    r = requests.get(url,
        # Reuse their bearer token
        headers=request.headers)
    donors = r.json()['results']
    print(donors)

    # Will need to look into how to go about this -- ideally we implement this into the SQL in Katsu's side
    # NB: Double check if using URLs like below are weak to an injection attack when I'm not tired
    filters = [
        (treatment, f"{config.KATSU_URL}/v2/authorized/treatments/?treatment_type=%s"),
        (chemotherapy, f"{config.KATSU_URL}/v2/authorized/chemotherapies/?drug_name=%s"),
        (immunotherapy, f"{config.KATSU_URL}/v2/authorized/immunotherapies/?drug_name=%s"),
        (hormone_therapy, f"{config.KATSU_URL}/v2/authorized/hormone_therapies/?drug_name=%s")
    ]
    for (this_filter, url) in filters:
        if this_filter != "":
            print(this_filter)
            permissible_donors = get_donors_from_katsu(
                url,
                this_filter
            )
            donors = [donor for donor in donors if donor['submitter_donor_id'] in permissible_donors]
            print(donors)
    
    # TODO: Cache the above list of donor IDs, then return donor_with_clinical_data per-page as a response
    # Add prev and next parameters to the repsonse, appending a session ID.
    # Essentially we want to go session ID -> list of donors
    # and then paginate the list of donors, calling donors_with_clinical_data on each before returning
    return str(donors), 200
