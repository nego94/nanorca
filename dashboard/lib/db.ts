import postgres from "postgres";

// Single shared connection pool. Next.js caches module-level singletons
// across requests in the same process (dev: per-file hot reload aware).
const sql = postgres(process.env.DATABASE_URL!, {
  max: 5,
  idle_timeout: 20,
  connect_timeout: 10,
});

export default sql;
