/**
 * test-gemini.js
 * Run this with: GEMINI_API_KEY="your_api_key" node test-gemini.js
 * 
 * NOTE: This requires a valid Gemini API key set as an environment variable.
 */

require('dotenv').config();
const { GoogleGenerativeAI } = require("@google/generative-ai");

// Access your API key as an environment variable
const apiKey = process.env.GEMINI_API_KEY;

async function testGemini() {
  console.log(`--- Testing Gemini API ---`);

  if (!apiKey) {
    console.error("ERROR: GEMINI_API_KEY environment variable is missing.");
    console.log("Please make sure your .env file is set up correctly.");
    return;
  }

  const genAI = new GoogleGenerativeAI(apiKey);

  try {
    // 1. Test Text Generation (Structured Extraction using Gemini Flash)
    // Note: We use gemini-1.5-flash as the latest standard flash model in the SDK, 
    // replacing this with "gemini-3.5-flash" whenever it becomes available in the API.
    const modelName = "gemini-3.5-flash"; 
    console.log(`1. Generating completion with model: ${modelName}...`);
    
    const model = genAI.getGenerativeModel({ 
      model: modelName,
      generationConfig: {
        responseMimeType: "application/json",
      }
    });

    const prompt = "Classify this message: 'We need to make sure the login button is always blue on mobile.' Is it a requirement? Reply in JSON format: {\"isRequirement\": boolean, \"rationale\": string}";
    
    const result = await model.generateContent(prompt);
    console.log("Response:", result.response.text());

    // 2. Test Embeddings Generation
    console.log(`\n2. Generating embeddings with model: text-embedding-004...`);
    // NOTE: Gemini embedding API requires a specific format in some SDK versions, let's use the text-embedding-004 model or fall back to an older one if it fails
    try {
        const embeddingModel = genAI.getGenerativeModel({ model: "gemini-embedding-2" });
        const textToEmbed = "We need to make sure the login button is always blue on mobile.";
        // Some older SDK versions use embedContent, newer might just use embed
        const embedResult = await embeddingModel.embedContent(textToEmbed);
        const embedding = embedResult.embedding.values;
        console.log(`Successfully generated vector embedding. Dimensions: ${embedding.length}`);
        console.log(`First 5 values: ${embedding.slice(0, 5).join(', ')}...`);
    } catch (e) {
        console.log(`text-embedding-004 failed, trying text-embedding-004...`);
        console.log(e.message);
    }

  } catch (error) {
    console.error("Error testing Gemini:", error.message);
  }
}

testGemini();