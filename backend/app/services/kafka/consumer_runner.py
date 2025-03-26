import asyncio
from app.services.kafka.consumer import KafkaConsumer

async def main():
    consumer = KafkaConsumer(
        bootstrap_servers="192.168.200.148:9092",  # Kafka broker address
        topic="maf-tasks",  # Kafka topic
    )
    await consumer.start()
    await consumer.consume()

if __name__ == "__main__":
    asyncio.run(main())
