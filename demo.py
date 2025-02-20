import json
import os
import asyncio
import requests
import logging
import time
from openai import OpenAI
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

client = OpenAI(
    api_key = os.environ.get("OPENAI_API_KEY")
)

TARGET_API_URL = os.environ.get("TARGET_API_URL", "https://playground.mprompto.com:3000/api/v1/demo/clients/load-json-data")


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)

def generate_bulk_questions(raw_text, question_prompt, num_questions=20, model="gpt-4o"):

    bulk_prompt = (
        f"Using the following context, generate exactly {num_questions} unique, concise, and use-case–driven questions. "
        "Each question must be a single sentence that starts with a capital letter and ends with a question mark. "
        "Return the questions as a JSON array of strings (do not include any extra text).\n\n"
        f"Context:\n{raw_text}"
    )
    
    messages = [
        {"role": "system", "content": question_prompt},
        {"role": "user", "content": bulk_prompt}
    ]
    
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=1000,
            temperature=0.2
        )
        raw_output = response.choices[0].message.content.strip()
        logging.info(f"Raw output: {raw_output}")
        if raw_output.startswith("```json"):
            raw_output = raw_output[len("```json"):].strip()
        if raw_output.endswith("```"):
            raw_output = raw_output[:-3].strip()
        
        logging.info("Bulk questions generated. Parsing JSON...")
        questions = json.loads(raw_output)
        if not (isinstance(questions, list) and len(questions) == num_questions):
            raise ValueError("Parsed questions do not match the expected count.")
        return questions
    except Exception as e:
        logging.error(f"Error generating bulk questions: {e}")
        return None

def generate_answer_for_question(raw_text, question, answer_prompt, model="o1-mini"):
    
    prompt = (
        f"Using the following context, answer the question below.\n\n"
        f"Question:\n{question}\n\n"
        "Your answer must include\n"
        "1. containing a detailed, highly professional answer addressing the question.\n"
        "2. 'Reasoning:\n"
        "    - 'Facet considered:' followed by facets considered to answer the question (single line answer, short but informative),\n"
        "    - 'Pros considered:' followed by exactly THREE advantages (comma-separated),\n"
        "    - 'Cons considered:' followed by exactly TWO drawbacks (comma-separated).\n\n"
        f"Context:\n{raw_text}\n\n"
        "Generate your answer as plain text with the two sections and no extra commentary."
    )
    
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": answer_prompt}
    ]
    
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=600,
            temperature=0.3
        )
        answer_text = response.choices[0].message.content.strip()
        logging.info("Answer generated for question.")
        return answer_text
    except Exception as e:
        logging.error(f"Error generating answer for question '{question}': {e}")
        return None

def extract_answer_details(answer_text, question, model="o1-preview"):
    
    extraction_prompt = (
        '''You are an expert at extracting structured information. The following question was asked:
{question}
Below is an answer text generated by an LLM in response to this question. The answer text includes a brief summary of the question and relevant context data, along with a clearly and logically presented analysis, a balanced view explicitly stating the pros and cons, and a well-reasoned recommendation that aligns with the user's needs. 

Your job is to extract 4 things from it:
- The main response (which is a professional, concise, two-sentence response similar to what a veteran shop attendant might say).
- The facet (whatever multiple facets of the product was considered during decision-making, explain in a nice manner, in 1 sentence).
- The pros (exactly 3 points, present the pros beautifully, they are the key selling point, 1 sentence per point).
- The cons (exactly 2 points, present them beautifully, 1 sentence per point).

Return the result in exactly the following JSON format:
{{
 "answers": "<the main concise response>",
 "facet": ["<facets>"],
 "pros": ["<pro1>", "<pro2>", "<pro3>"],
 "cons": ["<con1>", "<con2>"]
}}
Do not include any additional commentary. Use only the text provided below.
Text: {answer_text}'''.format(question=question, answer_text=answer_text)
    )
    
    messages = [
        {"role": "system", "content": "You are a precise data extraction assistant."},
        {"role": "user", "content": extraction_prompt}
    ]
    
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=300,
            temperature=0.2
        )
        extracted_raw = response.choices[0].message.content.strip()
        logging.info(f"Extraction raw output: {extracted_raw}")
        
        if extracted_raw.startswith("```json"):
            extracted_raw = extracted_raw[len("```json"):].strip()
        if extracted_raw.endswith("```"):
            extracted_raw = extracted_raw[:-3].strip()
        
        if not extracted_raw:
            raise ValueError("Extraction output is empty after cleaning.")
        
        data = json.loads(extracted_raw)
        # Validate counts:
        if not (isinstance(data.get("facet"), list) and len(data.get("facet")) == 1):
            raise ValueError("Facet count error.")
        if not (isinstance(data.get("pros"), list) and len(data.get("pros")) == 3):
            raise ValueError("Pros count error.")
        if not (isinstance(data.get("cons"), list) and len(data.get("cons")) == 2):
            raise ValueError("Cons count error.")
        if not data.get("answers"):
            raise ValueError("Main answer is empty.")
        return data
    except Exception as e:
        logging.error(f"Error extracting answer details: {e}")
        return None

def generate_final_qna_container(container_id, raw_text, question_prompt, answer_prompt, num_pairs=20, model = 'gpt-4o'):
    
    final_container = {
        "id": container_id,
        "question_prompt": question_prompt,
        "answer_prompt": answer_prompt,
        "data": {
            "qa": []
        }
    }
    
    questions = generate_bulk_questions(raw_text, question_prompt, num_questions = 20, model = model)
    if not questions:
        logging.error("Failed to generate questions.")
        return None
    logging.info(f"Generated {len(questions)} questions. Proceeding with answer generation...")
    
    for idx, question in enumerate(questions, start=1):
        logging.info(f"Processing Q&A pair {idx}...")
        answer_text = generate_answer_for_question(raw_text, question, answer_prompt, model=model)
        if not answer_text:
            logging.error(f"Skipping Q&A pair {idx} due to answer generation failure.")
            continue
        
        extracted = extract_answer_details(answer_text, question, model)
        if not extracted:
            logging.error(f"Skipping Q&A pair {idx} due to extraction failure.")
            continue
        
        qa_pair = {
            "question": question,
            "answers": extracted.get("answers"),
            "facet": extracted.get("facet"),
            "pros": extracted.get("pros"),
            "cons": extracted.get("cons")
        }
        final_container["data"]["qa"].append(qa_pair)
        
        time.sleep(1)
    
    if len(final_container["data"]["qa"]) != num_pairs:
        logging.warning(f"Expected {num_pairs} QA pairs but only assembled {len(final_container['data']['qa'])}.")
    
    return json.dumps(final_container, indent=2)

# ---------------------------
# FastAPI Setup
# ---------------------------
app = FastAPI()

class QNARequest(BaseModel):
    id: str
    raw_text: str
    question_prompt: str
    answer_prompt: str

# We'll implement asynchronous processing here.
# We'll store job status in a global dictionary.
jobs = {}

def process_qna_job(job_id: str, raw_text: str, question_prompt: str, answer_prompt: str):
    """Background task that processes the Q&A generation job."""
    final_json = generate_final_qna_container(job_id, raw_text, question_prompt, answer_prompt, num_pairs=20, model="gpt-4")
    if final_json:
        # Optionally push to target API.
        try:
            headers = {"Content-Type": "application/json"}
            push_response = requests.post(TARGET_API_URL, data=final_json, headers=headers)
            if push_response.status_code != 200:
                logging.error(f"Failed to push Q&A container for job {job_id}. Status: {push_response.status_code}")
        except Exception as e:
            logging.error(f"Error pushing Q&A container for job {job_id}: {e}")
        jobs[job_id] = {"status": "completed", "result": final_json}
    else:
        jobs[job_id] = {"status": "failed", "result": None}

@app.post("/api/generate")
async def generate_qna(request: QNARequest, background_tasks: BackgroundTasks):
    logging.info("Received QNA generation request.")
    job_id = request.id  # Using the provided id as the job ID.
    jobs[job_id] = {"status": "processing", "result": None}
    
    # Offload the long-running job to the background.
    background_tasks.add_task(process_qna_job, job_id, request.raw_text, request.question_prompt, request.answer_prompt)
    
    # Return immediately with the job ID and a processing status.
    return {"job_id": job_id, "status": "processing"}

@app.get("/api/status/{job_id}")
def get_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job ID not found")
    return jobs[job_id]

# ---------------------------
# Server Deployment
# ---------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
