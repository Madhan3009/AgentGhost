/**
 * test-bullmq.js
 * Run this with: node test-bullmq.js
 * 
 * NOTE: Ensure you have started Redis via docker-compose:
 *   docker-compose up -d redis
 */

const { Queue, Worker } = require('bullmq');
const IORedis = require('ioredis');

// Connect to the local Redis container
const connection = new IORedis({
  host: '127.0.0.1',
  port: 6380,
  maxRetriesPerRequest: null
});

async function testBullMQ() {
  console.log(`--- Testing Redis + BullMQ ---`);
  
  const QUEUE_NAME = 'requirement-processing-queue';

  // 1. Initialize the Queue (Publisher)
  const myQueue = new Queue(QUEUE_NAME, { connection });

  // 2. Initialize the Worker (Consumer)
  const worker = new Worker(QUEUE_NAME, async (job) => {
    console.log(`\n[Worker] Picked up Job ID: ${job.id}`);
    console.log(`[Worker] Job Name: ${job.name}`);
    console.log(`[Worker] Data payload:`, job.data);
    
    // Simulate processing delay (e.g., calling an LLM)
    await new Promise(resolve => setTimeout(resolve, 1000));
    
    console.log(`[Worker] Finished processing Job ID: ${job.id}`);
    return { status: "success", parsed: true };
  }, { connection });

  // Event listeners for worker
  worker.on('completed', (job, returnvalue) => {
    console.log(`[Worker Event] Job ${job.id} has completed! Result:`, returnvalue);
  });

  worker.on('failed', (job, err) => {
    console.log(`[Worker Event] Job ${job.id} has failed with ${err.message}`);
  });

  // 3. Add a job to the queue
  console.log("Adding job to queue...");
  await myQueue.add('analyze_slack_message', {
    rawMessageId: 'msg-12345',
    text: 'We need to make sure the login button is always blue on mobile.'
  });

  console.log("Job added. Waiting for worker to process...");

  // Let it run for a bit, then shut down
  setTimeout(async () => {
    console.log("\nShutting down test...");
    await worker.close();
    await myQueue.close();
    connection.quit();
    process.exit(0);
  }, 2500);
}

testBullMQ();