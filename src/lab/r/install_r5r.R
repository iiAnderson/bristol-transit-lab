# Install the pinned r5r from CRAN into the transit-lab-r env (plans/P2.md D7).
options(repos = c(CRAN = "https://cloud.r-project.org"))
remotes::install_version("r5r", version = "2.4.0", upgrade = "never", quiet = TRUE)
library(r5r)
cat("r5r", as.character(packageVersion("r5r")), "\n")
