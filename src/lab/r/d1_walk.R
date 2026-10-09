# D1 spike (plans/P3.md): walk-only travel times in r5r with a terrain raster in the
# network folder, to compare with r5py on the same raster and cost function.
# Args: one JSON file with net_dir, elevation ("NONE" | "TOBLER" | "MINETTI"), origins,
# destinations, departure, walk_speed_kmh, max_trip_min, java_mem, out.
suppressMessages({library(jsonlite)})
cfg <- fromJSON(commandArgs(trailingOnly = TRUE)[1])
options(java.parameters = paste0("-Xmx", cfg$java_mem))
suppressMessages({library(r5r); library(data.table); library(arrow)})
dep <- as.POSIXct(cfg$departure, format = "%Y-%m-%d %H:%M", tz = "Europe/London")
o <- fread(cfg$origins); d <- fread(cfg$destinations)
net <- build_network(cfg$net_dir, verbose = FALSE, elevation = cfg$elevation)
x <- travel_time_matrix(net, origins = o, destinations = d, mode = "WALK",
                        departure_datetime = dep, walk_speed = cfg$walk_speed_kmh,
                        max_trip_duration = cfg$max_trip_min, progress = FALSE)
write_parquet(x, cfg$out)
cat("rows", nrow(x), "\n")
