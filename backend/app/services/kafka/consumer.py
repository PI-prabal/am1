import json
import asyncio
import logging
from datetime import datetime
import aiokafka
from app.schemas.kafka import KafkaTaskRequest
from app.core.enums import MultiAgentFrameworks, TaskStatus
from app.services.maf.impl.am1 import AM1
from app.services.maf.impl.magentic_one import MagenticOne
from app.services.maf.impl.ag2 import AG2
from app.schemas.task import AgenticTaskRequest

logger = logging.getLogger(__name__)

class KafkaConsumer:
    def __init__(self, bootstrap_servers, topic, group_id="background_processor", max_parallel_tasks=3):
        self.bootstrap_servers = bootstrap_servers
        self.topic = topic
        self.group_id = group_id
        self.consumer = None
        self.running = False
        self.max_parallel_tasks = max_parallel_tasks
        self.semaphore = asyncio.Semaphore(max_parallel_tasks)

    async def start(self):
        self.consumer = aiokafka.AIOKafkaConsumer(
            self.topic,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            value_deserializer=lambda m: json.loads(m.decode("utf-8"))
        )
        await self.consumer.start()
        logger.info(f"Kafka consumer started for topic {self.topic}")
        self.running = True

    async def stop(self):
        if self.consumer:
            self.running = False
            await self.consumer.stop()
            logger.info(f"Kafka consumer stopped for topic {self.topic}")

    async def process_job(self, job_data):
        """Process a job from the Kafka topic."""
        # Convert ISO datetime string back to datetime object
        job_data["created_at"] = datetime.fromisoformat(job_data["created_at"])
        job = KafkaTaskRequest(**job_data)

        logger.info(f"Processing job {job.task_id} of type {job.task_type}")

        try:
            # Process the job depending on the task type
            if job.task_type in [MultiAgentFrameworks.MAGENTIC_ONE.value, MultiAgentFrameworks.AG2.value,
                                 MultiAgentFrameworks.AM1.value]:
                await self._process_maf_job(job)
            else:
                logger.warning(f"Unknown job type: {job.task_type}")

            # Update job status to completed
            job.status = "completed"
            logger.info(f"KafkaTaskRequest {job.task_id} processed successfully")

        except Exception as e:
            # Update job status to failed
            job.status = "failed"
            logger.error(f"Error processing job {job.task_id}: {str(e)}")  # Handle failure queue here

        # TODO: update a database with the job status
        return job

    async def _process_maf_job(self, job: KafkaTaskRequest):
        """Process a MAF job using the appropriate multi-agent framework."""
        logger.info(f"Running MAF task for job {job.task_id} with framework {job.task_type}")

        # Initialize the corresponding framework based on job data
        if job.task_type == MultiAgentFrameworks.MAGENTIC_ONE.value:
            maf = MagenticOne()
        elif job.task_type == MultiAgentFrameworks.AG2.value:
            maf = AG2()
        elif job.task_type == MultiAgentFrameworks.AM1.value:
            maf = AM1()
        else:
            logger.error(f"Unsupported framework: {job.task_type}")
            return  # If the framework is not supported, exit early

        # Create the request object for the agentic task
        agentic_task_request = AgenticTaskRequest(
            task_id=job.task_id,
            query=job.query,
            llm_model=job.llm_model,
            enable_internet=job.enable_internet,
        )
        # Assuming other required attributes like files are included in the job payload

        # Execute the task using the framework
        try:
            task_response = await maf.start_task(agentic_task_request)
            logger.info(f"Task with ID {job.task_id} finished. Status: {task_response.status}")
        except Exception as e:
            logger.error(f"Error in running MAF for job {job.task_id}: {str(e)}")

    async def consume(self):
        """Consume messages from the Kafka topic and process them with parallelism."""
        if not self.consumer:
            raise RuntimeError("Consumer not started")

        try:
            async for message in self.consumer:
                if not self.running:
                    break

                logger.info(f"Received message from partition {message.partition} at offset {message.offset}")

                try:
                    job_data = message.value

                    # Acquire a semaphore slot to run parallel tasks
                    async with self.semaphore:
                        await self.process_job(job_data)

                    # Commit the offset after the job is processed
                    await self.consumer.commit()

                except Exception as e:
                    logger.error(f"Error processing message: {str(e)}")

        except Exception as e:
            logger.error(f"Consumer error: {str(e)}")
            if self.running:
                # Attempt to restart the consumer
                await self.stop()
                await asyncio.sleep(5)
                await self.start()
                asyncio.create_task(self.consume())
