import "dotenv/config";
import express from 'express';
import { dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const app = express(); 

app.set('views', __dirname + '/views');
app.set('view engine', 'ejs');
app.use(express.static('public')); 

app.get('/', (req, res) => {
  res.render("index.ejs");
});

app.get('/upload', (req, res) => {
  res.render("upload.ejs");
});

app.listen(3000, () => {
  console.log('Server is running on http://localhost:3000');
});