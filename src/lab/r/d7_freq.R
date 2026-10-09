# D7 measurement (plans/P3.md P3a-8): the expanded matrix on a network whose GTFS may
# hold frequencies.txt, aggregated per pair over every departure minute and draw.
# Args: one JSON file with net_dir, origins, destinations, departure, window_min,
# draws_per_minute, max_rides, walk_speed_kmh, max_trip_min, route_id, java_mem, out.
suppressMessages({library(jsonlite)})
cfg <- fromJSON(commandArgs(trailingOnly = TRUE)[1])
options(java.parameters = paste0("-Xmx", cfg$java_mem))
suppressMessages({library(r5r); library(data.table); library(arrow)})
dep <- as.POSIXct(cfg$departure, format = "%Y-%m-%d %H:%M", tz = "Europe/London")
o <- fread(cfg$origins); d <- fread(cfg$destinations)
net <- build_network(cfg$net_dir, verbose = FALSE)
res <- list()
for (k in split(seq_len(nrow(o)), ceiling(seq_len(nrow(o)) / 20))) {
  x <- expanded_travel_time_matrix(net, origins = o[k], destinations = d,
                                   mode = c("WALK", "TRANSIT"), departure_datetime = dep,
                                   time_window = cfg$window_min, breakdown = TRUE,
                                   draws_per_minute = cfg$draws_per_minute,
                                   max_rides = cfg$max_rides, walk_speed = cfg$walk_speed_kmh,
                                   max_trip_duration = cfg$max_trip_min, progress = FALSE)
  setDT(x)
  x <- x[!is.na(total_time)]
  x[, ride := !is.na(n_rides) & n_rides > 0]
  x[, uses := ride & grepl(cfg$route_id, routes, fixed = TRUE)]
  res[[length(res) + 1]] <- x[, .(rows = .N, draws = uniqueN(draw_number),
                                  p50 = as.numeric(median(total_time)), total_mean = mean(total_time),
                                  wait_mean = mean(wait_time[ride]), ride_share = mean(ride),
                                  route_share = mean(uses)), by = .(from_id, to_id)]
}
write_parquet(rbindlist(res), cfg$out)
cat("pairs", sum(sapply(res, nrow)), "\n")
stop_r5(net)
