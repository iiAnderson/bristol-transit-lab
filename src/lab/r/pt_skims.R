# PT skims from r5r's expanded travel time matrix (plans/P2.md D7, C1a; SPEC §7.1).
#
# Usage: Rscript pt_skims.R <config.json>
# config: net_dir, origins (csv id,lat,lon), destinations (csv id,lat,lon),
#         departure ("YYYY-mm-dd HH:MM", Europe/London), window_min, max_rides,
#         walk_speed_kmh, max_walk_min (null = none), max_trip_min, reach_share_min,
#         chunk, sample_ids (csv id; full per-minute output kept for these origins),
#         out_dir, java_mem
#
# Per origin chunk, one row per (OA, destination) pair:
#   mean access/wait/ride/transfer/egress minutes, mean n_rides and mean transfers
#   (max(n_rides − 1, 0) per minute, so GC stays linear) over reachable
#   minutes (walk-only minutes: access = total, the rest 0), p25/p50/p75 total over
#   all window minutes (unreachable = Inf, so a percentile beyond the reachable share is
#   NA), best-minute total, reachable share, walk-only share (of reachable minutes),
#   top-3 routes by minutes used, unreachable flag (reachable share < threshold).
suppressMessages({library(jsonlite)})
cfg <- fromJSON(commandArgs(trailingOnly = TRUE)[1])
options(java.parameters = paste0("-Xmx", cfg$java_mem))
suppressMessages({library(r5r); library(data.table); library(arrow)})

dep <- as.POSIXct(cfg$departure, format = "%Y-%m-%d %H:%M", tz = "Europe/London")
o <- fread(cfg$origins); d <- fread(cfg$destinations)
sample_ids <- if (!is.null(cfg$sample_ids)) fread(cfg$sample_ids)$id else character(0)
dir.create(cfg$out_dir, recursive = TRUE, showWarnings = FALSE)
net <- build_network(cfg$net_dir, verbose = FALSE)
W <- cfg$window_min
max_walk <- if (is.null(cfg$max_walk_min)) Inf else cfg$max_walk_min

pctl <- function(x, p) {             # nearest-rank over W minutes, Inf for unreachable
  v <- sort(c(x, rep(Inf, W - length(x))))
  r <- v[ceiling(p * W)]
  ifelse(is.finite(r), r, NA_real_)
}
top_routes <- function(routes, n_rides) {
  rr <- routes[n_rides > 0 & !is.na(routes)]
  if (!length(rr)) return(NA_character_)
  t <- sort(table(rr), decreasing = TRUE)
  paste(head(names(t), 3), collapse = ";")
}

chunks <- split(seq_len(nrow(o)), ceiling(seq_len(nrow(o)) / cfg$chunk))
t_all <- Sys.time()
for (k in seq_along(chunks)) {
  f_out <- file.path(cfg$out_dir, sprintf("chunk_%04d.parquet", k))
  if (file.exists(f_out)) next
  oc <- o[chunks[[k]]]
  x <- expanded_travel_time_matrix(net, origins = oc, destinations = d,
                                   mode = c("WALK", "TRANSIT"), departure_datetime = dep,
                                   time_window = W, breakdown = TRUE,
                                   max_rides = cfg$max_rides, walk_speed = cfg$walk_speed_kmh,
                                   max_walk_time = max_walk,
                                   max_trip_duration = cfg$max_trip_min, progress = FALSE)
  setDT(x)
  x <- x[!is.na(total_time)]
  walk_only <- x$n_rides == 0 | is.na(x$n_rides)
  x[walk_only, `:=`(access_time = total_time, wait_time = 0, ride_time = 0,
                    transfer_time = 0, egress_time = 0, n_rides = 0)]
  keep <- x[from_id %in% sample_ids]
  if (nrow(keep)) write_parquet(keep, file.path(cfg$out_dir, sprintf("minutes_%04d.parquet", k)))
  s <- x[, .(access_min = mean(access_time), wait_min = mean(wait_time),
             ride_min = mean(ride_time), transfer_min = mean(transfer_time),
             egress_min = mean(egress_time), n_rides = mean(n_rides),
             n_transfers = mean(pmax(n_rides - 1, 0)),
             p25 = pctl(total_time, 0.25), p50 = pctl(total_time, 0.50),
             p75 = pctl(total_time, 0.75), best_min = min(total_time),
             reach_share = .N / W, walk_only_share = mean(n_rides == 0),
             top_routes = top_routes(routes, n_rides)),
         by = .(from_id, to_id)]
  s[, unreachable := reach_share < cfg$reach_share_min]
  write_parquet(s, f_out)
  cat(sprintf("chunk %d/%d: %d origins, %d pairs, %.0f s elapsed\n", k, length(chunks),
              nrow(oc), nrow(s), as.numeric(difftime(Sys.time(), t_all, units = "secs"))))
}
stop_r5(net)
