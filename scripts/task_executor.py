import argparse
import ast
import datetime
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET

import prompts
from config import load_config
from and_controller import list_all_devices, AndroidController, traverse_tree
from model import parse_explore_rsp, parse_grid_rsp, OpenAIModel, QwenModel
from utils import print_with_color, draw_bbox_multi, draw_grid
from appium import webdriver
from appium.webdriver.common.mobileby import MobileBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from action_logger import log_action

arg_desc = "AppAgent Executor"
parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, description=arg_desc)
parser.add_argument("--app")
parser.add_argument("--root_dir", default="./")
args = vars(parser.parse_args())

configs = load_config()

if configs["MODEL"] == "OpenAI":
    mllm = OpenAIModel(base_url=configs["OPENAI_API_BASE"],
                       api_key=configs["OPENAI_API_KEY"],
                       model=configs["OPENAI_API_MODEL"],
                       temperature=configs["TEMPERATURE"],
                       max_tokens=configs["MAX_TOKENS"])
elif configs["MODEL"] == "Qwen":
    mllm = QwenModel(api_key=configs["DASHSCOPE_API_KEY"],
                     model=configs["QWEN_MODEL"])
else:
    print_with_color(f"ERROR: Unsupported model type {configs['MODEL']}!", "red")
    sys.exit()

app = args["app"]
root_dir = args["root_dir"]

if not app:
    print_with_color("What is the name of the app you want me to operate?", "blue")
    app = input()
    app = app.replace(" ", "")

app_dir = os.path.join(os.path.join(root_dir, "apps"), app)
work_dir = os.path.join(root_dir, "tasks")
if not os.path.exists(work_dir):
    os.mkdir(work_dir)
auto_docs_dir = os.path.join(app_dir, "auto_docs")
demo_docs_dir = os.path.join(app_dir, "demo_docs")
task_timestamp = int(time.time())
dir_name = datetime.datetime.fromtimestamp(task_timestamp).strftime(f"task_{app}_%Y-%m-%d_%H-%M-%S")
task_dir = os.path.join(work_dir, dir_name)
os.mkdir(task_dir)
log_path = os.path.join(task_dir, f"log_{app}_{dir_name}.txt")

no_doc = False
if not os.path.exists(auto_docs_dir) and not os.path.exists(demo_docs_dir):
    print_with_color(f"No documentations found for the app {app}. Do you want to proceed with no docs? Enter y or n",
                     "red")
    user_input = ""
    while user_input != "y" and user_input != "n":
        user_input = input().lower()
    if user_input == "y":
        no_doc = True
    else:
        sys.exit()
elif os.path.exists(auto_docs_dir) and os.path.exists(demo_docs_dir):
    print_with_color(f"The app {app} has documentations generated from both autonomous exploration and human "
                     f"demonstration. Which one do you want to use? Type 1 or 2.\n1. Autonomous exploration\n2. Human "
                     f"Demonstration",
                     "blue")
    user_input = ""
    while user_input != "1" and user_input != "2":
        user_input = input()
    if user_input == "1":
        docs_dir = auto_docs_dir
    else:
        docs_dir = demo_docs_dir
elif os.path.exists(auto_docs_dir):
    print_with_color(f"Documentations generated from autonomous exploration were found for the app {app}. The doc base "
                     f"is selected automatically.", "yellow")
    docs_dir = auto_docs_dir
else:
    print_with_color(f"Documentations generated from human demonstration were found for the app {app}. The doc base is "
                     f"selected automatically.", "yellow")
    docs_dir = demo_docs_dir

device_list = list_all_devices()
if not device_list:
    print_with_color("ERROR: No device found!", "red")
    sys.exit()
print_with_color(f"List of devices attached:\n{str(device_list)}", "yellow")
if len(device_list) == 1:
    device = device_list[0]
    print_with_color(f"Device selected: {device}", "yellow")
else:
    print_with_color("Please choose the Android device to start demo by entering its ID:", "blue")
    device = input()
controller = AndroidController(device)
width, height = controller.get_device_size()
if not width and not height:
    print_with_color("ERROR: Invalid device size!", "red")
    sys.exit()
print_with_color(f"Screen resolution of {device}: {width}x{height}", "yellow")

print_with_color("Please enter the description of the task you want me to complete in a few sentences:", "blue")
task_desc = input()

round_count = 0
last_act = "None"
task_complete = False
grid_on = False
rows, cols = 0, 0


def area_to_xy(area, subarea):
    area -= 1
    row, col = area // cols, area % cols
    x_0, y_0 = col * (width // cols), row * (height // rows)
    if subarea == "top-left":
        x, y = x_0 + (width // cols) // 4, y_0 + (height // rows) // 4
    elif subarea == "top":
        x, y = x_0 + (width // cols) // 2, y_0 + (height // rows) // 4
    elif subarea == "top-right":
        x, y = x_0 + (width // cols) * 3 // 4, y_0 + (height // rows) // 4
    elif subarea == "left":
        x, y = x_0 + (width // cols) // 4, y_0 + (height // rows) // 2
    elif subarea == "right":
        x, y = x_0 + (width // cols) * 3 // 4, y_0 + (height // rows) // 2
    elif subarea == "bottom-left":
        x, y = x_0 + (width // cols) // 4, y_0 + (height // rows) * 3 // 4
    elif subarea == "bottom":
        x, y = x_0 + (width // cols) // 2, y_0 + (height // rows) * 3 // 4
    elif subarea == "bottom-right":
        x, y = x_0 + (width // cols) * 3 // 4, y_0 + (height // rows) * 3 // 4
    else:
        x, y = x_0 + (width // cols) // 2, y_0 + (height // rows) // 2
    return x, y


while round_count < configs["MAX_ROUNDS"]:
    round_count += 1
    print_with_color(f"Round {round_count}", "yellow")
    screenshot_path = controller.get_screenshot(f"{dir_name}_{round_count}", task_dir)
    xml_path = controller.get_xml(f"{dir_name}_{round_count}", task_dir)
    if screenshot_path == "ERROR" or xml_path == "ERROR":
        break
    if grid_on:
        rows, cols = draw_grid(screenshot_path, os.path.join(task_dir, f"{dir_name}_{round_count}_grid.png"))
        image = os.path.join(task_dir, f"{dir_name}_{round_count}_grid.png")
        prompt = prompts.task_template_grid
    else:
        clickable_list = []
        focusable_list = []
        traverse_tree(xml_path, clickable_list, "clickable", True)
        traverse_tree(xml_path, focusable_list, "focusable", True)
        elem_list = clickable_list.copy()
        for elem in focusable_list:
            bbox = elem.bbox
            center = (bbox[0][0] + bbox[1][0]) // 2, (bbox[0][1] + bbox[1][1]) // 2
            close = False
            for e in clickable_list:
                bbox = e.bbox
                center_ = (bbox[0][0] + bbox[1][0]) // 2, (bbox[0][1] + bbox[1][1]) // 2
                dist = (abs(center[0] - center_[0]) ** 2 + abs(center[1] - center_[1]) ** 2) ** 0.5
                if dist <= configs["MIN_DIST"]:
                    close = True
                    break
            if not close:
                elem_list.append(elem)
        draw_bbox_multi(screenshot_path, os.path.join(task_dir, f"{dir_name}_{round_count}_labeled.png"), elem_list,
                        dark_mode=configs["DARK_MODE"])
        image = os.path.join(task_dir, f"{dir_name}_{round_count}_labeled.png")
        if no_doc:
            prompt = re.sub(r"<ui_document>", "", prompts.task_template)
        else:
            ui_doc = ""
            for i, elem in enumerate(elem_list):
                doc_path = os.path.join(docs_dir, f"{elem.uid}.txt")
                if not os.path.exists(doc_path):
                    continue
                ui_doc += f"Documentation of UI element labeled with the numeric tag '{i + 1}':\n"
                doc_content = ast.literal_eval(open(doc_path, "r").read())
                if doc_content["tap"]:
                    ui_doc += f"This UI element is clickable. {doc_content['tap']}\n\n"
                if doc_content["text"]:
                    ui_doc += f"This UI element can receive text input. The text input is used for the following " \
                              f"purposes: {doc_content['text']}\n\n"
                if doc_content["long_press"]:
                    ui_doc += f"This UI element is long clickable. {doc_content['long_press']}\n\n"
                if doc_content["v_swipe"]:
                    ui_doc += f"This element can be swiped directly without tapping. You can swipe vertically on " \
                              f"this UI element. {doc_content['v_swipe']}\n\n"
                if doc_content["h_swipe"]:
                    ui_doc += f"This element can be swiped directly without tapping. You can swipe horizontally on " \
                              f"this UI element. {doc_content['h_swipe']}\n\n"
            print_with_color(f"Documentations retrieved for the current interface:\n{ui_doc}", "magenta")
            ui_doc = """
            You also have access to the following documentations that describes the functionalities of UI 
            elements you can interact on the screen. These docs are crucial for you to determine the target of your 
            next action. You should always prioritize these documented elements for interaction:""" + ui_doc
            prompt = re.sub(r"<ui_document>", ui_doc, prompts.task_template)
    prompt = re.sub(r"<task_description>", task_desc, prompt)
    prompt = re.sub(r"<last_act>", last_act, prompt)
    print_with_color("Thinking about what to do in the next step...", "yellow")
    status, rsp = mllm.get_model_response(prompt, [image])

    if status:
        with open(log_path, "a") as logfile:
            log_item = {"step": round_count, "prompt": prompt, "image": f"{dir_name}_{round_count}_labeled.png",
                        "response": rsp}
            logfile.write(json.dumps(log_item) + "\n")
        if grid_on:
            res = parse_grid_rsp(rsp)
        else:
            res = parse_explore_rsp(rsp)
        act_name = res[0]
        if act_name == "FINISH":
            task_complete = True
            break
        if act_name == "ERROR":
            break
        last_act = res[-1]
        res = res[:-1]
        if act_name == "tap":
            _, area = res
            element = elem_list[area - 1]
            tl, br = element.bbox
            x, y = (tl[0] + br[0]) // 2, (tl[1] + br[1]) // 2
            ret = controller.tap(x, y)
            if ret == "ERROR":
                print_with_color("ERROR: tap execution failed", "red")
                break
            
            # 액션 로그 생성 및 저장
            log_action(xml_path, round_count, "tap", element, app, root_dir, "success" if ret != "ERROR" else "failed")
        elif act_name == "text":
            _, input_str = res
            element = elem_list[0]  # text 입력은 첫 번째 요소를 사용
            ret = controller.text(input_str)
            if ret == "ERROR":
                print_with_color("ERROR: text execution failed", "red")
                break
            
            # 액션 로그 생성 및 저장
            log_action(xml_path, round_count, "text", element, app, root_dir, "success" if ret != "ERROR" else "failed", input_text=input_str)
        elif act_name == "long_press":
            _, area = res
            element = elem_list[area - 1]
            tl, br = element.bbox
            x, y = (tl[0] + br[0]) // 2, (tl[1] + br[1]) // 2
            ret = controller.long_press(x, y)
            if ret == "ERROR":
                print_with_color("ERROR: long press execution failed", "red")
                break
            
            # 액션 로그 생성 및 저장
            log_action(xml_path, round_count, "long_press", element, app, root_dir, "success" if ret != "ERROR" else "failed")
        elif act_name == "swipe":
            _, area, swipe_dir, dist = res
            element = elem_list[area - 1]
            tl, br = element.bbox
            x, y = (tl[0] + br[0]) // 2, (tl[1] + br[1]) // 2
            ret = controller.swipe(x, y, swipe_dir, dist)
            if ret == "ERROR":
                print_with_color("ERROR: swipe execution failed", "red")
                break
            
            # 액션 로그 생성 및 저장
            log_action(xml_path, round_count, "swipe", element, app, root_dir, "success" if ret != "ERROR" else "failed", 
                      direction=swipe_dir, distance=dist)
        elif act_name == "grid":
            grid_on = True
        elif act_name == "tap_grid" or act_name == "long_press_grid":
            _, area, subarea = res
            x, y = area_to_xy(area, subarea)
            if act_name == "tap_grid":
                ret = controller.tap(x, y)
                if ret == "ERROR":
                    print_with_color("ERROR: tap execution failed", "red")
                    break
            else:
                ret = controller.long_press(x, y)
                if ret == "ERROR":
                    print_with_color("ERROR: tap execution failed", "red")
                    break
        elif act_name == "swipe_grid":
            _, start_area, start_subarea, end_area, end_subarea = res
            start_x, start_y = area_to_xy(start_area, start_subarea)
            end_x, end_y = area_to_xy(end_area, end_subarea)
            ret = controller.swipe_precise((start_x, start_y), (end_x, end_y))
            if ret == "ERROR":
                print_with_color("ERROR: tap execution failed", "red")
                break
        if act_name != "grid":
            grid_on = False
        time.sleep(configs["REQUEST_INTERVAL"])
    else:
        print_with_color(rsp, "red")
        break

if task_complete:
    print_with_color("Task completed successfully", "yellow")
elif round_count == configs["MAX_ROUNDS"]:
    print_with_color("Task finished due to reaching max rounds", "yellow")
else:
    print_with_color("Task finished unexpectedly", "red")

class TestReplay:
    def setup_method(self):
        caps = {
            'platformName': 'Android',
            'automationName': 'UiAutomator2',
            'deviceName': 'Android Device'
        }
        self.driver = webdriver.Remote('http://localhost:4723/wd/hub', caps)

    def find_element_with_fallback(self, element_info):
        """여러 방법으로 요소 찾기 시도"""
        try:
            # 1. resource-id로 시도
            if element_info["resource_id"]:
                return self.driver.find_element(MobileBy.ID, element_info["resource_id"])
        except:
            try:
                # 2. content-desc로 시도
                if element_info["content_desc"]:
                    return self.driver.find_element(MobileBy.ACCESSIBILITY_ID, 
                                                  element_info["content_desc"])
            except:
                try:
                    # 3. class와 bounds로 시도
                    xpath = f"//{element_info['class_name']}[@bounds='{element_info['bounds']}']"
                    return self.driver.find_element(MobileBy.XPATH, xpath)
                except:
                    # 4. 좌표로 마지막 시도
                    return None

    def execute_action(self, action_info, element=None):
        """액션 실행"""
        if action_info["type"] == "tap":
            if element:
                element.click()
            else:
                x, y = action_info["parameters"]["coordinates"]
                self.driver.tap([(x, y)])
        elif action_info["type"] == "swipe":
            params = action_info["parameters"]
            self.driver.swipe(params["start_coordinates"][0],
                            params["start_coordinates"][1],
                            params["direction"],
                            params["distance"])
        # ... 다른 액션들

    def test_replay(self):
        """로그 파일 재생"""
        with open('action_log.json') as f:
            log_data = json.load(f)

        for step in log_data["steps"]:
            # 현재 화면 확인
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((MobileBy.XPATH, "//*"))
            )

            # 요소 찾기
            element = self.find_element_with_fallback(step["element"])
            
            # 액션 실행
            self.execute_action(step["action"], element)

            # 결과 검증
            assert step["action"]["status"] == "success"

def create_action_log(xml_path, action_type, element, status="success"):
    """액션 로그 생성"""
    log_entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "screen": {
            "xml_path": xml_path,
            "screen_name": get_screen_name(xml_path),  # XML 분석해서 현재 화면 이름 추출
        },
        "element": {
            "index": element.index if hasattr(element, 'index') else None,
            "resource_id": element.attrib.get("resource-id", ""),
            "class_name": element.attrib.get("class", ""),
            "content_desc": element.attrib.get("content-desc", ""),
            "bounds": element.attrib.get("bounds", ""),
            "clickable": element.attrib.get("clickable", ""),
            "scrollable": element.attrib.get("scrollable", ""),
            "focused": element.attrib.get("focused", ""),
        },
        "action": {
            "type": action_type,  # tap, swipe, text, long_press
            "parameters": get_action_parameters(action_type, element),
            "status": status
        }
    }
    return log_entry

def get_screen_name(xml_path):
    """XML 분석해서 현재 화면 이름 추출"""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    # 예: 패키지명이나 activity 이름으로 화면 구분
    package = root.attrib.get("package", "")
    activity = root.attrib.get("activity", "")
    return f"{package}.{activity}"

def get_action_parameters(action_type, element):
    """액션 타입별 파라미터 생성"""
    if action_type == "tap":
        return {
            "coordinates": get_element_center(element.attrib["bounds"])
        }
    elif action_type == "swipe":
        return {
            "start_coordinates": get_element_center(element.attrib["bounds"]),
            "direction": element.swipe_direction,
            "distance": element.swipe_distance
        }
    elif action_type == "text":
        return {
            "input_text": element.input_text
        }
    elif action_type == "long_press":
        return {
            "coordinates": get_element_center(element.attrib["bounds"]),
            "duration": 1000  # milliseconds
        }
    return {}

def get_element_center(bounds_str):
    """요소의 중앙 좌표 계산"""
    bounds = bounds_str[1:-1].split("][")
    x1, y1 = map(int, bounds[0].split(","))
    x2, y2 = map(int, bounds[1].split(","))
    return [(x1 + x2) // 2, (y1 + y2) // 2]
