const express = require("express");

const app = express();
const port = process.env.PORT || 3000;
const mongoUrl = process.env.MONGO_URL;

app.get("/health", (_req, res) => {
  res.json({ status: "ok", mongoUrl });
});

app.listen(port, () => {
  console.log(`listening on ${port}`);
});
