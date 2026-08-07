library(readr)
library(dplyr)

carbon_df <- read.csv("~/Documents/GitHub/crossborder_carbon/data_collection/wcc_projects_border_counties_historic.csv")
View(carbon_df)
table(carbon_df$validator_name)

print(carbon_df$project_id[1]) # is this loss for the ID? 

sprintf("%.0f", carbon_df$project_id[1]) # okay, so the IDs are intact; 

carbon_df <- read.csv(
  "wcc_projects_border_counties_historic.csv",
  colClasses = c(entity_id = "character", project_id = "character")
)

carbon_df_enriched <- read.csv("~/Documents/GitHub/crossborder_carbon/data_collection/wcc_projects_border_counties_enriched.csv")

# Probably need to clean this up; but it's okay for right now... 
colnames(carbon_df_enriched)
columns_to_remove <- c("unit_city", "unit_latitude", "unit_longitude",
                       "unit_validator_name", "unit_grid_reference", "unit_country_name",
                       "project_type_name", "project_duration_years", "detail_community_score",
                       "detail_economy_score", "detail_water_score", "detail_biodiversity_score")
carbon_df_cleaned <- carbon_df_enriched %>%
  select((-all_of(columns_to_remove)))

write_csv(carbon_df_cleaned, "~/Documents/GitHub/crossborder_carbon/data_collection/finalised_border_data.csv")
