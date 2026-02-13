import os
import json
import sys
from typing import List, Dict, Optional, Tuple

# Add parent directory to path to reach config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DATA_DIR

class QuestionEngine:
    """
    Handles path resolution and loading of quiz questions from the JSON data repository.
    Strictly follows the RN.json pattern and curriculum mapping.
    """
    
    BASE_DATA_DIR = DATA_DIR
    
    @staticmethod
    def resolve_path(subject: str, grade: str, unit: str, round_num: int) -> str:
        """
        Resolves the absolute path to a specific R-file.
        Example: data/Biology/Grade_10/Unit_1/R1.json
        """
        fs_grade = grade.replace(" ", "_")
        
        # Unit in blueprint is "Unit 1: Sub-fields of Biology"
        # Filesystem uses "Unit_1"
        unit_folder = unit.split(":")[0].strip().replace(" ", "_")
        
        filename = f"R{round_num}.json"
        
        path = os.path.join(
            QuestionEngine.BASE_DATA_DIR,
            subject,
            fs_grade,
            unit_folder,
            filename
        )
        return path

    @staticmethod
    def load_unit_questions(subject: str, grade: str, unit: str) -> Tuple[List[Dict], Dict, str]:
        """
        Loads ALL available questions for a unit (aggregating R1, R2, R3...).
        """
        all_questions = []
        final_state = {}
        unit_title = unit
        
        # Loop through rounds 1 to 10 (reasonable limit)
        for r in range(1, 11):
            path = QuestionEngine.resolve_path(subject, grade, unit, r)
            if not os.path.exists(path):
                break
                
            if os.path.getsize(path) == 0:
                print(f"[ENGINE] Skipping empty file: {path}")
                continue
                
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if not content:
                        continue
                    data = json.loads(content)
                    batch_questions = data.get("questions", [])
                    all_questions.extend(batch_questions)
                    if data.get("unit"):
                        unit_title = data.get("unit")
                    if not final_state:
                        final_state = data.get("__STATE__", {})
            except Exception as e:
                print(f"Error loading questions from {path}: {e}")
                continue # Try next round instead of breaking
        
        return all_questions, final_state, unit_title

    @staticmethod
    def load_batch(subject: str, grade: str, unit: str, round_num: int) -> Tuple[Optional[List[Dict]], Optional[Dict], Optional[str]]:
        """
        Loads a batch of questions and its state from a JSON file.
        Returns (questions_list, state_dict, full_unit_title)
        """
        path = QuestionEngine.resolve_path(subject, grade, unit, round_num)
        
        if not os.path.exists(path):
            return None, None, None
            
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get("questions", []), data.get("__STATE__", {}), data.get("unit")
        except Exception as e:
            print(f"Error loading questions from {path}: {e}")
            return None, None, None

    @staticmethod
    def get_progress_info(state: Dict) -> Dict:
        """
        Extracts progress metrics from the batch state.
        """
        metrics = state.get("content_density_metrics", {})
        phase_status = state.get("phase_status", {})
        
        return {
            "progress_percentage": metrics.get("structural_coverage_percentage", 0),
            "phase1_complete": phase_status.get("phase1_complete", False),
            "total_questions": state.get("last_question_id", "").split("_Q")[-1] if "last_question_id" in state else 0
        }

    @staticmethod
    def list_grades(subject: str) -> List[str]:
        """
        Lists available grades for a subject.
        Returns: ["Grade 9", "Grade 10", ...]
        """
        subject_dir = os.path.join(QuestionEngine.BASE_DATA_DIR, subject)
        if not os.path.exists(subject_dir):
            return []
            
        grades = []
        for d in os.listdir(subject_dir):
            if d.startswith("Grade_") and os.path.isdir(os.path.join(subject_dir, d)):
                grades.append(d.replace("_", " "))
        
        # Sort Grade 12 down to Grade 9
        def sort_key(s):
            parts = s.split(" ")
            return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0

        grades.sort(key=sort_key, reverse=True)
        return grades

    @staticmethod
    def list_units(subject: str, grade: str) -> List[str]:
        """
        Lists available units for a subject and grade.
        Returns: ["Unit 1", "Unit 2", ...]
        """
        fs_grade = grade.replace(" ", "_")
        grade_dir = os.path.join(QuestionEngine.BASE_DATA_DIR, subject, fs_grade)
        if not os.path.exists(grade_dir):
            return []
            
        units = []
        for d in os.listdir(grade_dir):
            if d.startswith("Unit_") and os.path.isdir(os.path.join(grade_dir, d)):
                units.append(d.replace("_", " "))
        
        # Sort Unit 1, Unit 2...
        def sort_key(s):
            parts = s.split(" ")
            return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0

        units.sort(key=sort_key)
        return units

    @staticmethod
    def find_question_by_id(question_id: str) -> Optional[Dict]:
        """
        Locates a question across all subjects/grades/units based on its ID.
        Format: G9_Bio_U1_Q001
        """
        parts = question_id.split("_")
        if len(parts) < 4: return None
        
        grade_str = parts[0].replace("G", "Grade ")
        subj_code = parts[1] # Bio, Chem, Phys, Math
        unit_num = parts[2].replace("U", "") # 1
        unit_str = f"Unit {unit_num}"
        
        subj_map = {"Bio": "Biology", "Chem": "Chemistry", "Phys": "Physics", "Math": "Mathematics"}
        subject = subj_map.get(subj_code)
        if not subject: return None

        units = QuestionEngine.list_units(subject, grade_str)
        target_unit = next((u for u in units if u.startswith(unit_str)), None)
        
        if not target_unit: return None

        questions, _, _ = QuestionEngine.load_unit_questions(subject, grade_str, target_unit)
        for q in questions:
            if q.get("question_id") == question_id:
                return q
        return None
