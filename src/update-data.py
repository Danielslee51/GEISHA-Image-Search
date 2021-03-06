"""
File: update-data.py
Author: Daniel Lee <danielslee@email.arizona.edu>
Description: Updates the saved embryo data/predictions with the results from new embryos as they are created.
This script is intended for the Geisha server, and is run as a cron job to keep the search engine up to date.

For security purposes, the folder containing embryo images on the GEISHA database is not exposed in this
code. Instead, a command-line argument is required that specifies the local filepath to the embryo images.
The usage is as such:

python src/update-data.py <image home directory> # e.g. /home/geisha/images

Updating the image information involves these steps:
* Pulling the list of images that have been created since the last update. The date of the last update
is stored in the "last-updated" file, and new image metadata is downloaded from Geisha.
* Generating stage and anatomical locations predictions on the new images, using the trained stage and
locations models.
* Updating the saved information with the new entries (and logging results)
"""


from datetime import date
import os
import urllib
import pandas as pd
import pickle
from fastai.vision import *
import sys

# Grab image home directory from command line
if len(sys.argv) == 1:
    raise TypeError("Command line argument required specifying the directory containg embryo images")
elif len(sys.argv) == 2:
    image_home_dir = sys.argv[1]

# Change working directory to src/
current_file_filepath = os.path.abspath(__file__)
dname = os.path.dirname(current_file_filepath)
os.chdir(dname)
try:
    os.mkdir("temporary-downloads")
except FileExistsError:
    pass

# Find out when data was last updated, download information about the new images that have been created
with open("last-updated", "r") as file:
    last_updated = file.read()

print("Checking for new images")
new_images_metadata_url = "http://geisha.arizona.edu/geisha/Images/metadata?scope=public&since=" + last_updated
new_images_metadata_path = "temporary-downloads/new_images.csv"
urllib.request.urlretrieve(new_images_metadata_url, new_images_metadata_path)
new_image_metadata = pd.read_csv(new_images_metadata_path, names = ["fname", "stage", "locations"])
new_image_fnames = new_image_metadata.fname.values

# Load existing data
with open("../data/database-image-predictions.pkl", "rb") as inp:
    database_image_filenames, database_image_stages, database_image_locations = pickle.load(inp)

# Remove any new images that are already saved (due to overlap in filtering) or not on disk (errors in metadata)
images_to_remove = np.array([], dtype=object)
for fname in new_image_fnames:
    already_saved = fname in database_image_filenames
    doesnt_exist = not os.path.exists(image_home_dir+fname)
    if already_saved or doesnt_exist:
        images_to_remove = np.append(images_to_remove, fname)

new_image_fnames = np.setdiff1d(new_image_fnames, images_to_remove)

# Begin update if new images exist
if len(new_image_fnames) > 0:

    # Create databunch containing new images (a bit inefficient here, but it works)
    new_image_df = pd.DataFrame({"fname": new_image_fnames, "Label": range(len(new_image_fnames))})
    bs = min(64, len(new_image_fnames))
    new_image_data = (ImageList.from_df(new_image_df, path=image_home_dir)
                  .split_none()
                  .label_from_df(1, label_cls=FloatList)
                  .transform(tfms=([], []), size=(400,300))
                  .databunch(bs=bs).normalize(imagenet_stats))
    print("Calculating new predictions")

    # Load models
    stage_model = load_learner("../models/","stage-prediction-model.pkl")
    locations_model = load_learner("../models/","locations-prediction-model.pkl")
    locations_model.model.eval()
    stage_model.model.eval()
    stage_model.data = new_image_data
    locations_model.data = new_image_data

    # Run inference and get predictions
    new_stage_preds = stage_model.get_preds(new_image_data.train_dl)
    new_locations_preds = locations_model.get_preds(new_image_data.train_dl)

    # Add new filenames, stage predictions, and locations predictions to existing ones
    database_image_filenames = np.append(database_image_filenames, new_image_fnames)
    database_image_stages = torch.cat((database_image_stages, new_stage_preds[0]))
    database_image_locations = torch.cat((database_image_locations, new_locations_preds[0]))
    assert len(new_image_fnames) == len(new_stage_preds[0])
    assert len(new_stage_preds[0]) == len(new_locations_preds[0])

    # Save results
    def save_object(obj, filename="../data/database-image-predictions.pkl"):
        """Saved a python object to the given file using pickle"""
        with open(filename, 'wb') as output:  # Overwrites any existing file.
            pickle.dump(obj, output, pickle.HIGHEST_PROTOCOL)
        return
    updated_database_image_information = [database_image_filenames, database_image_stages, database_image_locations]
    # Data is changed below here
    save_object(updated_database_image_information)

    # Update logs
    current_date = date.today().strftime("%m/%d/%y")
    with open("last-updated", "w") as file:
        file.write(current_date)
    with open("data-updates-log", "a") as file:
        file.write(f"{current_date}: Added {len(new_image_fnames)} images ({' '.join(list(new_image_fnames))})\n")
    print(f"Updated with {len(new_image_fnames)} images")

else:
    print("Data already up to date")