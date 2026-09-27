# D7 step 1 (plans/P2.md): runtime and output of r5r::expanded_travel_time_matrix().
# Usage: Rscript d7_expanded.R <net_dir> <origins.csv> <destinations.csv> <YYYY-mm-dd HH:MM> <window_min> <out.parquet>
args <- commandArgs(trailingOnly = TRUE)
options(java.parameters = "-Xmx8G")
suppressMessages({library(r5r); library(data.table); library(arrow)})
net_dir <- args[1]; dep <- as.POSIXct(args[4], format = "%Y-%m-%d %H:%M", tz = "Europe/London")
win <- as.integer(args[5])
o <- fread(args[2]); d <- fread(args[3])
t0 <- Sys.time()
net <- build_network(net_dir, verbose = FALSE)
t_build <- as.numeric(difftime(Sys.time(), t0, units = "secs"))
t0 <- Sys.time()
x <- expanded_travel_time_matrix(net, origins = o, destinations = d,
                                 mode = c("WALK", "TRANSIT"), departure_datetime = dep,
                                 time_window = win, breakdown = TRUE,
                                 max_trip_duration = 120, max_rides = 8, progress = FALSE)
t_ettm <- as.numeric(difftime(Sys.time(), t0, units = "secs"))
write_parquet(x, args[6])
cat(sprintf("r5r %s; build %.1f s; expanded matrix %.1f s; rows %d; columns %s\n",
            packageVersion("r5r"), t_build, t_ettm, nrow(x), paste(names(x), collapse = ",")))
stop_r5(net)
