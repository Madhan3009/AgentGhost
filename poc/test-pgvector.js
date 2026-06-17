/**
 * test-pgvector.js
 * Run this with: node test-pgvector.js
 * 
 * NOTE: Ensure you have started PostgreSQL via docker-compose:
 *   docker-compose up -d postgres
 */

const { Client } = require('pg');

async function testPgVector() {
  console.log(`--- Testing PostgreSQL + pgvector ---`);
  
  const client = new Client({
    connectionString: 'postgres://ghost:ghostpassword@localhost:5433/ghost_poc',
  });

  try {
    await client.connect();
    console.log("Connected to PostgreSQL.");

    // 1. Enable pgvector extension
    await client.query('CREATE EXTENSION IF NOT EXISTS vector;');
    console.log("Extension 'vector' enabled.");

    // 2. Create a test table
    await client.query(`
      DROP TABLE IF EXISTS test_items;
      CREATE TABLE test_items (
        id SERIAL PRIMARY KEY,
        text_content TEXT,
        embedding VECTOR(3)
      );
    `);
    console.log("Test table created with VECTOR column.");

    // 3. Insert mock vectors
    // In reality, this would be a 1536-dim array from Ollama or OpenAI
    await client.query(`
      INSERT INTO test_items (text_content, embedding) VALUES 
      ('Login button color', '[0.1, 0.2, 0.3]'),
      ('Checkout page layout', '[0.8, 0.7, 0.9]'),
      ('Session timeout fix', '[0.2, 0.1, 0.4]')
    `);
    console.log("Mock items inserted.");

    // 4. Perform a vector similarity search (cosine distance: <=>)
    const targetVector = '[0.15, 0.25, 0.35]'; // Close to 'Login button color'
    console.log(`\nSearching for vectors closest to: ${targetVector}`);
    
    const { rows } = await client.query(`
      SELECT text_content, 
             1 - (embedding <=> $1) as similarity_score
      FROM test_items 
      ORDER BY embedding <=> $1 
      LIMIT 2;
    `, [targetVector]);

    console.log("\nResults:");
    rows.forEach((row, i) => {
      console.log(`${i + 1}. ${row.text_content} (Similarity: ${(row.similarity_score * 100).toFixed(2)}%)`);
    });

  } catch (error) {
    console.error("Error testing pgvector:", error);
  } finally {
    await client.end();
  }
}

testPgVector();