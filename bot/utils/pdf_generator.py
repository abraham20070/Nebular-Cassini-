import os
from fpdf import FPDF
from datetime import datetime

class PDFGenerator(FPDF):
    def __init__(self, subject, grade, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.subject = subject
        self.grade = grade
        self.set_auto_page_break(auto=True, margin=15)
        
        # Colors
        self.primary_color = (33, 150, 243)  # Material Blue
        self.secondary_color = (25, 118, 210) # Darker Blue
        self.text_color = (33, 33, 33)
        self.muted_text = (117, 117, 117)
        self.white = (255, 255, 255)
        
        # UTF-8 Support via System Font (Cross-Platform for Render/Windows)
        self.unicode_enabled = False
        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", # Render Default
            "C:\\Windows\\Fonts\\DejaVuSans.ttf", # Windows if installed
            "C:\\Windows\\Fonts\\arial.ttf", # Windows Fallback
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", # Linux Fallback
            os.path.join(os.path.dirname(__file__), "fonts", "DejaVuSans.ttf") 
        ]
        
        target_font = None
        for path in font_paths:
            if os.path.exists(path):
                target_font = path
                break
        
        if target_font:
            try:
                # fpdf2 does NOT use uni=True, it handles it automatically
                self.add_font("CustomFont", "", target_font)
                
                # Check for bold version
                bold_path = target_font.replace(".ttf", "-Bold.ttf").replace("Sans", "Sans-Bold")
                if os.path.exists(bold_path):
                    self.add_font("CustomFont", "B", bold_path)
                else:
                    self.add_font("CustomFont", "B", target_font)

                self.font_family = "CustomFont"
                self.unicode_enabled = True
                print(f"[PDF] UTF-8 Font Loaded: {target_font}")
            except Exception as e:
                print(f"[PDF] Font Error: {e}")
        
        self.default_font = "CustomFont" if self.unicode_enabled else "helvetica"

    def sanitize_text(self, text):
        """Clean text. If Unicode is enabled, we keep most chars."""
        if not text: return "N/A"
        if not self.unicode_enabled:
            # Fallback for latin-1
            replacements = {
                '\u201c': '"', '\u201d': '"', '\u2018': "'", '\u2019': "'",
                '\u2013': '-', '\u2014': '-', '\u2026': '...'
            }
            for char, repl in replacements.items():
                text = text.replace(char, repl)
            return text.encode('latin-1', 'replace').decode('latin-1')
        return text # Keep as is if Arial loaded

    def header(self):
        if self.page_no() == 1:
            return # No header on cover page
        
        self.set_fill_color(*self.primary_color)
        self.rect(0, 0, 210, 20, 'F')
        
        self.set_y(5)
        self.set_font(self.default_font, 'B', 10)
        self.set_text_color(*self.white)
        self.cell(0, 10, f'NEBULAR CASSINI MCQ PRACTICE | {self.subject.upper()} - {self.grade.upper()}', align='C')
        self.ln(15)

    def footer(self):
        self.set_y(-15)
        self.set_font(self.default_font, 'I', 8)
        self.set_text_color(*self.muted_text)
        self.cell(0, 10, f'Page {self.page_no()} | Created by Nebular Cassini MCQ Bot', align='L')
        self.cell(0, 10, 'www.nebular-cassini.com', align='R')

    def create_cover_page(self, title):
        self.add_page()
        
        # Background Style
        self.set_fill_color(*self.primary_color)
        self.rect(0, 0, 210, 100, 'F')
        
        self.set_y(40)
        self.set_font(self.default_font, 'B', 28)
        self.set_text_color(*self.white)
        self.multi_cell(0, 15, self.sanitize_text(title.upper()), align='C')
        
        self.set_y(120)
        self.set_font(self.default_font, 'B', 18)
        self.set_text_color(*self.primary_color)
        self.cell(0, 10, f'{self.subject}', ln=True, align='C')
        self.set_font(self.default_font, '', 14)
        self.cell(0, 10, f'{self.grade}', ln=True, align='C')
        
        self.set_y(180)
        self.set_draw_color(*self.primary_color)
        self.set_line_width(0.5)
        self.line(50, 180, 160, 180)
        
        self.set_y(190)
        self.set_font(self.default_font, 'I', 12)
        self.set_text_color(*self.muted_text)
        self.multi_cell(0, 7, "This practice document contains curated MCQ questions, \ndetailed explanations, and strategic insights aligned with \nthe official Ethiopian curriculum.", align='C')
        
        self.set_y(260)
        self.set_font(self.default_font, 'B', 10)
        self.set_text_color(*self.primary_color)
        self.cell(0, 10, f'Generated on: {datetime.now().strftime("%B %d, %Y")}', align='C')

    def add_unit_divider(self, unit_name):
        self.add_page()
        self.set_y(100)
        self.set_font(self.default_font, 'B', 24)
        self.set_text_color(*self.primary_color)
        self.cell(0, 20, self.sanitize_text(unit_name.upper()), border='B', ln=True, align='C')
        self.ln(10)

    def add_questions_section(self, questions):
        self.set_font(self.default_font, '', 11)
        self.set_text_color(*self.text_color)
        
        for i, q in enumerate(questions):
            # Question Box
            self.set_font(self.default_font, 'B', 11)
            self.set_fill_color(245, 245, 245)
            self.multi_cell(0, 8, f'QUESTION {i+1}', fill=True)
            
            self.set_font(self.default_font, '', 11)
            self.ln(2)
            self.multi_cell(0, 6, self.sanitize_text(q.get("question", q.get("question_stem", "N/A"))))
            self.ln(3)
            
            options = q.get("options", {})
            for opt, val in options.items():
                self.set_x(20)
                self.set_font(self.default_font, 'B', 10)
                self.cell(10, 6, f'{opt}: ', ln=False)
                self.set_font(self.default_font, '', 10)
                self.multi_cell(0, 6, self.sanitize_text(str(val)))
            
            self.ln(8)
            if self.get_y() > 250:
                self.add_page()

    def add_answer_key(self, questions):
        self.add_page()
        self.set_font(self.default_font, 'B', 18)
        self.set_text_color(*self.primary_color)
        self.cell(0, 15, 'ANSWER KEY & DETAILED EXPLANATIONS', border='B', ln=True, align='C')
        self.ln(10)
        
        for i, q in enumerate(questions):
            self.set_font(self.default_font, 'B', 12)
            self.set_text_color(*self.secondary_color)
            self.cell(0, 8, f'Question {i+1}: Correct Answer [{q.get("correct_answer", "N/A")}]', ln=True)
            
            self.set_font(self.default_font, 'I', 10)
            self.set_text_color(*self.muted_text)
            explanation = q.get("explanation", "No explanation available for this item.")
            self.multi_cell(0, 6, f'Explanation: {self.sanitize_text(explanation)}')
            self.ln(6)
            self.set_draw_color(230, 230, 230)
            self.line(self.get_x(), self.get_y(), self.get_x() + 190, self.get_y())
            self.ln(4)
            
            if self.get_y() > 250:
                self.add_page()

# Caching directory
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache", "pdfs")
os.makedirs(CACHE_DIR, exist_ok=True)

def generate_unit_pdf(subject, grade, unit_name, questions, output_path):
    # Check Cache first
    cache_key = f"{subject}_{grade}_{unit_name}".replace(" ", "_").replace(":", "_")
    cached_file = os.path.join(CACHE_DIR, f"{cache_key}.pdf")
    
    if os.path.exists(cached_file):
        print(f"[PDF CACHE] Using cached file: {cached_file}")
        import shutil
        shutil.copy(cached_file, output_path)
        return output_path

    pdf = PDFGenerator(subject, grade)
    pdf.create_cover_page(f"MCQ Practice Guide: \n{unit_name}")
    pdf.add_page() # Content Page
    pdf.add_questions_section(questions)
    pdf.add_answer_key(questions)
    pdf.output(output_path)
    
    # Save to Cache
    import shutil
    shutil.copy(output_path, cached_file)
    return output_path

def generate_all_units_pdf(subject, grade, unit_data_list, output_path):
    # Cache for "Comprehensive" guides
    cache_key = f"COMPREHENSIVE_{subject}_{grade}_{len(unit_data_list)}".replace(" ", "_")
    cached_file = os.path.join(CACHE_DIR, f"{cache_key}.pdf")

    if os.path.exists(cached_file):
        import shutil
        shutil.copy(cached_file, output_path)
        return output_path

    pdf = PDFGenerator(subject, grade)
    pdf.create_cover_page(f"Comprehensive Subject Guide")
    
    for unit_title, questions in unit_data_list:
        pdf.add_unit_divider(unit_title)
        pdf.add_questions_section(questions)
    
    pdf.add_page()
    pdf.set_font(pdf.default_font, 'B', 20)
    pdf.cell(0, 20, "ANSWER KEYS", ln=True, align='C')
    
    for unit_title, questions in unit_data_list:
        pdf.set_font(pdf.default_font, 'B', 14)
        pdf.cell(0, 10, f"Unit: {unit_title}", ln=True)
        pdf.add_answer_key(questions)
        
    pdf.output(output_path)
    
    # Save to Cache
    import shutil
    shutil.copy(output_path, cached_file)
    return output_path
