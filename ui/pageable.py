import abc
import logging
import math
import pickle
import os.path
from bookfile_list import BookFile_List
from xml.dom.minidom import parse
from utility import pin_nums_to_alphas, unicode_to_pin_num, alphas_to_pin_nums, find_files

log = logging.getLogger(__name__)


class PageableError(Exception):
    pass


class Pageable(object):
    __metaclass__ = abc.ABCMeta
    '''
    provides a common base class for the library and book objects content is
    provided as a list of pin numbers. Numbers are those used in unicode
    braille patterns: http://en.wikipedia.org/wiki/Braille_Patterns

    :param content: a list of pin numbers for the page to display
    :param dimensions: dimensions of the display
    :param ui: ui is passed in so that the driver can be used directly
    '''

    def __init__(self, content, dimensions, ui):
        self.dimensions = dimensions
        self.ui = ui
        self.cells = dimensions[0]
        self.rows = dimensions[1]
        self.page = 0
        self.content = content
        self.load_state()

    def delete_content(self):
        '''remove all content'''
        self.content = []

    def get_num_pages(self):
        '''get number of pages in the content'''
        return int(math.ceil(len(self.content) / float(self.rows)))

    def add_new_item(self, item):
        '''add a new item to the content, sort by alpha

        :param item: a list of pin numbers
        '''
        self.content.append(item)
        # sort by alpha, have to convert pin nums back to alphas to make the comparison
        self.content = sorted(self.content, key = pin_nums_to_alphas)

    @abc.abstractmethod
    def get_state_file(self):
        '''return the name of the state file'''
        return

    def remove_state(self):
        log.info("removing state file %s" % self.get_state_file())
        try:
            os.remove(self.get_state_file())
        except OSError:
            pass

    def save_state(self):
        state = {
            'page': self.page,
            }
        with open(self.get_state_file(), 'w') as fh:
            pickle.dump(state, fh)

    def load_state(self):
        try:
            with open(self.get_state_file()) as fh:
                state = pickle.load(fh)
                self.page = state['page']
        except IOError:
            log.debug("no state file")

    def fit_content(self, content):
        '''
        used for making ascii text ready for showing as a line of braille:

        * shorten if necessary
        * pad to correct length if necessary
        '''
        return_text = []
        for line in content:
            while len(line) < self.cells:
                line.append(0)
            else:
                line = line[0:self.cells]
            return_text.append(line)

        # pad if necessary
        missing_rows = self.rows - len(return_text)
        log.debug("padding %d missing rows with %d spaces" % (missing_rows, self.cells * missing_rows))
        return_text.append([0] * self.cells * missing_rows)
        return return_text


    def show(self):
        '''
        returns a page of braille, represented by a list of numbers
        deals with pagination and dimensions
        '''
        start_line = self.page * self.rows
        end_line = self.page * self.rows + self.rows - 1
        if end_line > len(self.content) - 1:
            end_line = len(self.content) - 1

        log.info("showing page %d of %d (lines %d -> %d)" % (self.page + 1, self.get_num_pages(), start_line, end_line+1))

        output = []
        #get the right content
        output = self.content[start_line:end_line+1]

        # get everything the correct width & height
        output = self.fit_content(output)

        # flatten lists
        output = [cell for line in output for cell in line]

        # save the current page
        self.save_state()
        return output

    def home(self):
        '''return to beginning'''
        if self.page != 0:
            self.page = 0
        else:
            log.info("at home already")

    def prev(self):
        '''go to previous page'''
        if self.page > 0:
            self.page -= 1
        else:
            log.info("at home already")

    def end(self):
        '''go to end'''
        if self.get_num_pages() == 0:
            log.info("no content")
        elif self.page != self.get_num_pages() - 1:
            self.page = self.get_num_pages() - 1
        else:
            log.info("at end already")

    def next(self):
        '''go to next page'''
        if self.page < self.get_num_pages() - 1:
            self.page += 1
        else:
            log.info("at end already")


class Menu(Pageable):
    '''
    inherits from :class:`.Pageable`

    overrides __init__ so that we can create a menu

    '''

    def __init__(self, dimensions, config, ui):
        self.config = config
        self.ui = ui

        self.menu = [
            {'title' : 'refresh library', 'action': self.refresh},
            {'title' : 'shutdown', 'action': self.shutdown},
            {'title' : 'ip: %s' % self.get_ip_address(), 'action': self.ignore},
            ]

        menu_titles = [item["title"] for item in self.menu]
        menu_titles_brl = map(alphas_to_pin_nums, menu_titles)

        super(Menu, self).__init__(menu_titles_brl, dimensions, ui)

    def option(self, number):
        '''this is called by the UI to action a menu item'''
        menu_num = number + self.page * self.rows
        try:
            # call the correct action
            self.menu[menu_num]['action']()
        except IndexError:
            self.ui.driver.send_error_sound()

    def ignore(self):
        pass

    def get_ip_address(self):
        '''return the Pi's ip address'''
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("gmail.com",80))
            ip = (s.getsockname()[0])
            s.close()
            log.debug("ip: %s" % ip)
            return ip
        except socket.gaierror:
            return ''

    def shutdown(self):
        '''shutdown the Pi'''
        log.warning("shutdown")
        self.ui.driver.clear_page()
        os.system("sudo shutdown -h now")

    def refresh(self):
        '''wipe out the library state files and reload'''
        log.info("refresh library")
        # ask library to refresh
        self.ui.library.refresh()
        # send sound
        self.ui.driver.send_ok_sound()

    def get_state_file(self):
        '''return the name of the state file'''
        return 'menu.pkl'

Pageable.register(Menu)

class Library(Pageable):
    '''
    inherits from :class:`.Pageable`

    overrides __init__ so that we can load content from the library

    '''
    native_ext = '.canute'

    def __init__(self, dimensions, config, ui):
        self.config = config
        self.book_dir = self.config.get('files', 'library_dir')
        self.book_defs_file = self.book_dir + 'book_defs.pkl'

        self.load_book_defs()
        book_titles = [item["title"] for item in self.book_defs]
        book_titles_brl = map(alphas_to_pin_nums, book_titles)

        for book_title, number in zip(book_titles, range(len(book_titles))):
            log.debug("%03d - %s" % (number, book_title))

        super(Library, self).__init__(book_titles_brl, dimensions, ui)

        self.check_for_new_books()
        log.info("library has %d books available" % len(self.book_defs))

    def refresh(self):
        '''we had a bug to do with library additions where the state file got out of sequence'''
        self.remove_state()
        self.delete_content()
        self.check_for_new_books()

    def remove_state(self):
        self.book_defs = []
        super(Library, self).remove_state()

    def get_state_file(self):
        '''return the name of the state file'''
        return self.book_dir + 'library.pkl'

    def check_for_new_books(self):
        '''searchs the library directory for files that aren't in book_defs
        any new book is then added to the library
        '''
        filenames = find_files(self.book_dir, ('.pef', Library.native_ext))
        for book_def in self.book_defs:
            try:
                # remove existing books
                filenames.remove(book_def['filename'])
            except ValueError:
                # if book isn't in library
                pass

        for path in filenames:
            log.info("found new book file %s in %s" % (path, self.book_dir))
            self.add_book(path)

    def add_book(self, book_file, name=None, remove=True):
        '''add a new book to the library.
        If not in native format; convert and remove the old file.
        Conversion is done by file extension.

        :param path: the book's filename (including directory)
        :param name: names the book, if none give will be the file name
        '''

        # get the book's basename and extension
        basename, ext = os.path.splitext(os.path.basename(book_file))

        # do conversions based on extensions
        if ext == '.pef':
            log.info("converting pef to canute")
            native_file = self.book_dir + basename + Library.native_ext
            self.convert_pef(book_file, native_file)
            book_file = native_file
        elif ext == '.brf':
            log.info("converting brf to canute")
            native_file = self.book_dir + basename + Library.native_ext
            self.convert_brf(book_file, native_file, remove)
            book_file = native_file
        elif ext == Library.native_ext:
            log.info("book is already native format")
        else:
            raise PageableError("book %s in unknown format %s" % (book_file, ext))

        # if no name, make it from the filename
        if name is None:
            name = basename

        # create a new definition for the book
        book_def = {"title": name,
                    "filename": book_file,
                    "type": 'native'}
        log.info("adding new book [%s] to library" % (book_def["title"]))
        self.book_defs.append(book_def)

        # ensure they're in order by title
        self.book_defs = sorted(self.book_defs, key=lambda book: book['title'])

        # and save the definitions
        self.save_book_defs()

        # add the new title to our content, converting to pin numbers first
        # add_new_item sorts by alpha
        self.add_new_item(alphas_to_pin_nums(name))


    def convert_brf(self, brf_file, native_file, remove=True):
        '''
        converts a brf format braille book to native.

        :param brf: filename of the pef file
        :param native_file: filename of the destination file
        '''
        log.info("converting brf %s" % brf_file)

        # do the conversion from brf to pin numbers
        lines = []
        with open(brf_file) as fh:
            for in_line in fh.readlines():
                line = alphas_to_pin_nums(in_line)

                # ensure right length
                missing_cells = self.cells - len(line)
                line.extend([0] * missing_cells)

                lines.append(line)

        log.info("brf loaded with %d lines" % len(lines))
        log.info("writing to [%s]" % native_file)
        with open(native_file, 'w') as fh:
            for index,line in enumerate(lines):
                if len(line) > self.cells:
                    log.warning("length of row %d is %d which is greater than %d, truncating" % (index, len(line), self.cells))
                fh.write(bytearray(line[:self.cells]))

        if remove:
            log.info("removing old brf file")
            os.remove(brf_file)

    def convert_pef(self, pef_file, native_file):
        '''
        converts a pef format braille book (XML) to native.
        This format uses unicode of braille and uses the
        `unicode_to_pin_num()` function for the conversion to our own numbered braille pictures.

        :param pef_file: filename of the pef file
        :param native_file: filename of the destination file
        '''
        log.info("converting pef %s" % pef_file)
        try:
            xml_doc = parse(pef_file)
        except:
            os.remove(pef_file)
            log.error("could not convert %s" % pef_file)
            return


        rows = xml_doc.getElementsByTagName('row')
        log.debug("got %d rows" % len(rows))
        lines = []

        # do the conversion from unicode to pin numbers
        for row in rows:
            try:
                data = row.childNodes[0].data.rstrip()
                line = []
                for uni_char in data:
                    pin_num = unicode_to_pin_num(uni_char)
                    line.append(pin_num)
            except IndexError:
                # empty row element
                line = [0] * self.cells

            # ensure right length
            missing_cells = self.cells - len(line)
            line.extend([0] * missing_cells)

            lines.append(line)

        log.info("pef loaded with %d lines" % len(lines))
        log.info("writing to [%s]" % native_file)
        with open(native_file, 'w') as fh:
            for index,line in enumerate(lines):
                if len(line) > self.cells:
                    log.warning("length of row %d is %d which is greater than %d, truncating" % (index, len(line), self.cells))
                fh.write(bytearray(line[:self.cells]))

        log.info("removing old pef file")
        os.remove(pef_file)

    def load_book_defs(self):
        '''load book defs from a state file'''
        try:
            with open(self.book_defs_file) as fh:
                self.book_defs = pickle.load(fh)
        except IOError:
            self.book_defs = []

    def save_book_defs(self):
        '''save book defs to file'''
        with open(self.book_defs_file, 'w') as fh:
            pickle.dump(self.book_defs, fh)

    def get_book(self, book_num):
        '''
        creates and returns a :class:`.Book`

        :param book_num: integer representing the number of the book on the
        current page to load
        '''
        book_num = book_num + self.page * self.rows
        try:
            self.book_defs[book_num]["title"]
            self.book_defs[book_num]["type"]
        except IndexError:
            raise IndexError("no book at slot %d" % book_num)

        log.debug("loading book %d [%s]" % (book_num, self.book_defs[book_num]["title"]))
        book = Book(self.book_defs[book_num], self.dimensions, self.config, self.ui)
        return book

Pageable.register(Library)

class Book(Pageable):
    '''
    inherits from :class:`.Pageable`
    overide init with book loading stuff
    '''
    def __init__(self, book_def, dimensions, config, ui):
        self.book_def = book_def
        self.config = config
        self.cells = dimensions[0] # need this before we can load a book, and can't call super till we have content
        self.book_dir = self.config.get('files', 'library_dir')

        if book_def["type"] == 'native':
            content = BookFile_List(book_def["filename"], dimensions)
        else:
            raise PageableError("unknown book type %s" % book_def["type"])

        super(Book, self).__init__(content, dimensions, ui)
        log.info("book [%s] open at %d of %d pages" % (book_def["title"], self.page, self.get_num_pages()))

    def get_state_file(self):
        return self.book_dir + self.book_def['title'] + '.pkl'

    def next_chapter(self):
        '''next chapter'''
        log.info("next chapter")

    def prev_chapter(self):
        '''prev chapter'''
        log.info("prev chapter")

Pageable.register(Book)
