import datetime
import json
import os
import xml.etree.ElementTree as ET

def get_screen_name(xml_path):
    """XML 분석해서 현재 화면 이름 추출"""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    package = root.attrib.get("package", "")
    activity = root.attrib.get("activity", "")
    return f"{package}.{activity}"

def get_element_center(bounds_str):
    """요소의 중앙 좌표 계산"""
    bounds = bounds_str[1:-1].split("][")
    x1, y1 = map(int, bounds[0].split(","))
    x2, y2 = map(int, bounds[1].split(","))
    return [(x1 + x2) // 2, (y1 + y2) // 2]

def create_action_log(xml_path, action_type, element, status="success", **kwargs):
    """액션 로그 생성"""
    print(f"Debug - element type: {type(element)}")
    print(f"Debug - element: {element}")
    
    # element가 문자열인 경우 기본 속성으로 처리
    if isinstance(element, str):
        element_dict = {
            "class": "",
            "content-desc": "",
            "bounds": "[0,0][0,0]",
            "clickable": "false",
            "scrollable": "false",
            "focused": "false"
        }
        resource_id = element
    else:
        print(f"Debug - element.attrib: {element.attrib}")
        element_dict = element.attrib
        resource_id = element.uid

    print(f"Debug - element_dict: {element_dict}")
    
    # bounds 값이 없는 경우 기본값 사용
    bounds = element_dict.get("bounds", "[0,0][0,0]")
    if not bounds:
        bounds = "[0,0][0,0]"

    log_entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "screen": {
            "xml_path": xml_path,
            "screen_name": get_screen_name(xml_path),
        },
        "element": {
            "resource_id": resource_id,
            "class_name": element_dict.get("class", ""),
            "content_desc": element_dict.get("content-desc", ""),
            "bounds": bounds,
            "clickable": element_dict.get("clickable", ""),
            "scrollable": element_dict.get("scrollable", ""),
            "focused": element_dict.get("focused", ""),
        },
        "action": {
            "type": action_type,
            "parameters": {
                "coordinates": get_element_center(bounds)
            },
            "status": status
        }
    }
    
    # 추가 파라미터 처리
    if action_type == "text":
        log_entry["action"]["parameters"]["input_text"] = kwargs.get("input_text", "")
    elif action_type == "swipe":
        log_entry["action"]["parameters"]["direction"] = kwargs.get("direction", "")
        log_entry["action"]["parameters"]["distance"] = kwargs.get("distance", 0)
    
    return log_entry

def save_action_log(log_entry, app_name, root_dir="./"):
    """액션 로그를 파일에 저장"""
    # 로그 디렉토리 생성
    log_dir = os.path.join(root_dir, "apps", app_name, "actions")
    os.makedirs(log_dir, exist_ok=True)
    
    log_file = os.path.join(log_dir, 'action_log.json')
    
    try:
        with open(log_file, 'r+') as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = {"steps": []}
            data["steps"].append(log_entry)
            f.seek(0)
            f.truncate()
            json.dump(data, f, indent=2)
    except FileNotFoundError:
        with open(log_file, 'w') as f:
            json.dump({"steps": [log_entry]}, f, indent=2)

def log_action(xml_dir, round_count, action_type, element, app_name, root_dir="./", status="success", **kwargs):
    """액션 로깅을 위한 편의 함수"""
    xml_path = os.path.join(xml_dir, f"{round_count}.xml")
    if not os.path.exists(xml_path):
        print_with_color(f"Warning: XML file not found at {xml_path}", "yellow")
        return
        
    log_entry = create_action_log(xml_path, action_type, element, status, **kwargs)
    save_action_log(log_entry, app_name, root_dir) 