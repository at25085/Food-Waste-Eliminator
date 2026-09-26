import "dotenv/config";
import bodyParser from "body-parser";
import { dirname } from "path";
import { fileURLToPath } from "url";
import express from 'express';

const app = express(); 
app.use(express.static('public'));

app.get('/', (req, res) => {
  res.render("index.ejs");
});

app.listen(3000, () => {
  console.log('Server is running on http://localhost:3000');
});
