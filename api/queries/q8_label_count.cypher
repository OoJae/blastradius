// Boot-time census, run once at startup and never inside a request.
//
// Measured at 2,423 ms for :Package on the demo slice. Edge counts are
// deliberately absent: an unanchored count over the 1.3M-edge PKG_DEPENDS_ON is
// refused by admission control (250k intermediate-row cap), and /api/stats
// shows that refusal verbatim rather than hiding it.
MATCH (n:<<label>>)
RETURN count(*) AS n
