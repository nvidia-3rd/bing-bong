



    # if(q.qsize() < q.maxsize and is_queue_used):            
    #         q.put(chunk)

    #     elif(q.full):
    #         to_llm_tuple = calculate.summarize_emotions(q)
    #         image_analysis_array.append({"label": to_llm_tuple[0], "score": to_llm_tuple[1]})
            
    #         print(f"image_analysis_array size: {len(image_analysis_array)}")

    #         is_queue_used = False
    #         waste =  q.get();
    #         print(f"waste 감정 : {waste}")
                
    
    # if(stop_evt.is_set()):
    #     print(f"어떤 감정일까: {image_analysis_array}")    
    
    # if(q.qsize() == q.maxsize):
    #         is_queue_used = False

    #         while not q.empty():
    #             q.get_nowait()
    #         is_queue_used = True
