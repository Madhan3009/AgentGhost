require('dotenv').config();
const { GoogleGenerativeAI } = require("@google/generative-ai");
const genAI = new GoogleGenerativeAI(process.env.GEMINI_API_KEY);

async function run() {
  // We can just fetch via REST to see available models to be sure
  const res = await fetch(`https://generativelanguage.googleapis.com/v1beta/models?key=${process.env.GEMINI_API_KEY}`);
  const data = await res.json();
  console.log("Embedding Models available:");
  const embeddings = data.models.filter(m => m.name.includes("embed"));
  embeddings.forEach(m => console.log(m.name, m.supportedGenerationMethods));
}
run();
